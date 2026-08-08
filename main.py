import asyncio
import base64
import json
import random
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

DEFAULT_SCENES = {
    "撒娇": {
        "keywords": ["撒娇", "想你", "想你啦", "想你呀", "想你了", "亲亲", "抱抱", "mua", "爱你", "么么", "粘人", "贴贴"],
        "prompt": "可爱的二次元卡通少女撒娇表情包, 嘟嘴, 比心, 害羞, 红色腮红, 呆毛, 透明背景, 高清",
        "action": "嘟嘴撒娇, 双手比心, 害羞脸红, 眼睛弯成月牙",
        "weight": 1.0,
    },
    "晚安": {
        "keywords": ["晚安", "早安", "午安", "好梦", "睡觉", "睡吧", "休息吧", "睡啦", "困了"],
        "prompt": "可爱的二次元卡通人物晚安表情包, 困倦揉眼, 闭眼微笑, 头顶Z字睡眠符号, 抱着小枕头, 温馨治愈, 透明背景, 高清",
        "action": "困倦揉眼, 闭眼微笑, 头顶Z字睡眠符号, 抱着小枕头, 困得打哈欠",
        "weight": 1.0,
    },
    "开心": {
        "keywords": ["开心", "哈哈", "嘿嘿", "嘻嘻", "得意", "太棒了", "太赞", "棒棒", "耶", "笑死", "笑不活了", "哈哈哈"],
        "prompt": "二次元卡通人物开心大笑表情包, 眯眼笑成月牙, 双手比耶, 兴奋跳跃, 四周冒小星星, 透明背景, 高清",
        "action": "开心大笑, 眯眼笑成月牙, 双手比耶, 兴奋得跳起来, 四周冒小星星",
        "weight": 1.0,
    },
    "生气": {
        "keywords": ["生气", "气死", "讨厌", "哼", "无奈", "无语", "啊啊", "气气", "好气", "气鼓鼓", "哼唧"],
        "prompt": "二次元卡通人物生气表情包, 鼓起腮帮, 头上冒烟, 双手叉腰, 表情夸张可爱, 透明背景, 高清",
        "action": "气鼓鼓地鼓起腮帮, 头上冒烟, 双手叉腰, 撇过头",
        "weight": 1.0,
    },
    "安慰": {
        "keywords": ["安慰", "加油", "别难过", "没事的", "我在", "关心", "心疼", "抱抱你", "不哭", "摸摸头", "振作"],
        "prompt": "二次元卡通人物张开双臂温暖拥抱安慰表情包, 治愈系微笑, 周围漂浮粉红爱心, 透明背景, 高清",
        "action": "张开双臂求拥抱, 治愈系温柔微笑, 周围漂浮粉红爱心",
        "weight": 1.0,
    },
    "庆祝": {
        "keywords": ["庆祝", "恭喜", "生日快乐", "节日快乐", "新年", "圣诞", "新年快乐", "周年", "纪念日", "干杯", "贺喜"],
        "prompt": "二次元卡通人物庆祝表情包, 彩色纸屑飘落, 烟花, 蛋糕, 派对帽, 兴奋举手跳跃, 透明背景, 高清",
        "action": "兴奋举手欢呼跳跃, 彩色纸屑飘落, 烟花, 派对帽",
        "weight": 1.0,
    },
}


@register(
    "emoji_sticker",
    "anon",
    "用表情包图片代替文字, 让聊天更有温度",
    "1.0.0",
    "https://github.com/your-repo/astrbot_plugin_emoji_sticker",
)
class EmojiStickerPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # ---- API 配置 ----
        self.base_url = str(self.config.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = str(self.config.get("api_key", "") or "")
        self.model = str(self.config.get("model", "gpt-image-1"))
        self.image_size = str(self.config.get("image_size", "1024x1024"))
        self.timeout = int(self.config.get("timeout", 60) or 60)

        # ---- 触发配置 ----
        self.probability = max(0.0, min(1.0, float(self.config.get("trigger_probability", 0.3))))
        self.cooldown = max(0, int(self.config.get("cooldown_messages", 7) or 7))
        self.daily_limit = max(1, int(self.config.get("daily_limit", 3) or 3))
        self.send_mode = str(self.config.get("send_mode", "append"))
        # 失败自动优化重试
        self.auto_retry = bool(self.config.get("auto_retry", True))
        self.max_retries = max(1, int(self.config.get("max_retries", 3) or 3))

        # ---- 人设表情包配置 ----
        self.use_persona = bool(self.config.get("use_persona", True))
        self.persona_desc = str(self.config.get("persona_description", "") or "").strip()
        self.persona_style = str(self.config.get("persona_style", "") or "").strip()
        self.ref_upload = self.config.get("ref_image_upload", [])
        if isinstance(self.ref_upload, str):
            self.ref_upload = [self.ref_upload] if self.ref_upload else []
        self.identity_lock = (
            "必须严格以参考图作为同一人物身份基准, 保持参考图中的脸型、五官、发型发色、气质一致; "
            "只改变表情、动作、姿势和场景; 禁止重新设计成另一个人。"
        )

        # ---- 场景配置 ----
        self.scenes = self._load_scenes()

        # ---- 状态(内存) ----
        self._msg_since_last = 0
        self._daily_date: Optional[date] = None
        self._daily_counts: Dict[str, int] = {}
        self._sent_today: Dict[str, bool] = {}
        self._pending_tasks: List[asyncio.Task] = []
        self._last_text_signature: str = ""

        self._marker_re = re.compile(r"\[STICKER:([^\]\s]+)\]", re.IGNORECASE)

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------
    def _load_scenes(self) -> Dict[str, dict]:
        scenes = {k: dict(v) for k, v in DEFAULT_SCENES.items()}
        raw = self.config.get("scenes_json", "")
        if not raw:
            return scenes
        try:
            custom = json.loads(raw)
            if not isinstance(custom, dict):
                raise ValueError("scenes_json 必须是 JSON 对象")
            for name, cfg in custom.items():
                if isinstance(cfg, dict):
                    scenes[str(name)] = cfg
                else:
                    # 兼容简写: "撒娇": ["想你", "亲亲"]
                    scenes[str(name)] = {"keywords": list(cfg), "prompt": DEFAULT_SCENES.get(str(name), {}).get("prompt", f"二次元{name}表情包, 透明背景, 高清")}
            logger.info(f"[emoji_sticker] 场景配置已加载: {list(scenes.keys())}")
        except Exception as e:
            logger.error(f"[emoji_sticker] 场景配置解析失败, 使用默认配置: {e}")
        return scenes

    # ------------------------------------------------------------------
    # 每日重置
    # ------------------------------------------------------------------
    def _check_daily_reset(self):
        today = date.today()
        if self._daily_date != today:
            self._daily_date = today
            self._daily_counts = {}
            self._sent_today = {}

    # ------------------------------------------------------------------
    # 场景检测
    # ------------------------------------------------------------------
    def _detect_scene(self, text: str) -> Optional[str]:
        if not text:
            return None
        text_lower = text.lower()
        for scene_name, cfg in self.scenes.items():
            for kw in cfg.get("keywords", []) or []:
                kw = str(kw)
                if kw.lower() in text_lower:
                    return scene_name
        return None

    # ------------------------------------------------------------------
    # 频率控制
    # ------------------------------------------------------------------
    def _can_send(self, scene: str) -> bool:
        self._check_daily_reset()
        if self._msg_since_last < self.cooldown:
            return False
        if self._daily_counts.get(scene, 0) >= self.daily_limit:
            return False
        if self._sent_today.get(scene, False):
            return False
        return True

    def _record_send(self, scene: str):
        self._daily_counts[scene] = self._daily_counts.get(scene, 0) + 1
        self._sent_today[scene] = True
        self._msg_since_last = 0

    def _should_trigger(self, scene: str) -> bool:
        if not self._can_send(scene):
            return False
        weight = max(0.1, float(self.scenes.get(scene, {}).get("weight", 1.0)))
        p = min(1.0, self.probability * weight)
        return random.random() < p

    # ------------------------------------------------------------------
    # 图片生成(与 gpt_image_2_simple 同源: 参考图走 multipart edits, 回退 JSON generations)
    # ------------------------------------------------------------------
    async def _generate_image(self, prompt: str, ref_images: Optional[List[bytes]] = None) -> Optional[bytes]:
        if not self.api_key:
            logger.warning("[emoji_sticker] 未配置 api_key, 跳过图片生成")
            return None
        base = self.base_url
        if not base.endswith("/v1"):
            base += "/v1"
        has_ref = bool(ref_images)
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async def call_generations(session, with_ref: bool = False):
            url = f"{base}/images/generations"
            payload = {
                "model": self.model,
                "prompt": prompt,
                "n": 1,
                "size": self.image_size,
                "response_format": "b64_json",
            }
            if with_ref and ref_images:
                img = ref_images[0]
                mime = "image/png"
                if img.startswith(b"\xff\xd8"):
                    mime = "image/jpeg"
                elif img.startswith(b"RIFF"):
                    mime = "image/webp"
                data_uri = "data:%s;base64,%s" % (mime, base64.b64encode(img).decode())
                payload.update({"image": data_uri, "image_url": data_uri, "input_image": data_uri, "images": [data_uri]})
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"[emoji_sticker] 图片生成失败 HTTP {resp.status}: {err[:300]}")
                    return None
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    return None
                items = data.get("data") or []
                if not items:
                    return None
                first = items[0]
                b64 = first.get("b64_json")
                if b64:
                    return base64.b64decode(b64)
                img_url = first.get("url")
                if img_url:
                    async with session.get(img_url) as img_resp:
                        if img_resp.status == 200:
                            return await img_resp.read()
                return None

        async def call_edits(session):
            url = f"{base}/images/edits"
            form = aiohttp.FormData()
            form.add_field("model", self.model)
            form.add_field("prompt", prompt)
            form.add_field("n", "1")
            form.add_field("response_format", "b64_json")
            form.add_field("size", self.image_size)
            for i, img in enumerate((ref_images or [])[:4]):
                mime, ext = "image/png", "png"
                if img.startswith(b"\xff\xd8"):
                    mime, ext = "image/jpeg", "jpg"
                elif img.startswith(b"RIFF"):
                    mime, ext = "image/webp", "webp"
                field_name = "image" if i == 0 else "image[]"
                form.add_field(field_name, img, filename=f"ref_{i}.{ext}", content_type=mime)
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with session.post(url, data=form, headers=headers) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"[emoji_sticker] edits 失败 HTTP {resp.status}: {err[:200]}")
                    return None
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    return None
                items = data.get("data") or []
                if not items:
                    return None
                first = items[0]
                b64 = first.get("b64_json")
                if b64:
                    return base64.b64decode(b64)
                img_url = first.get("url")
                if img_url:
                    async with session.get(img_url) as img_resp:
                        if img_resp.status == 200:
                            return await img_resp.read()
                return None

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if has_ref:
                    result = await call_edits(session)
                    if result:
                        return result
                    logger.warning("[emoji_sticker] edits 失败, 回退 JSON generations")
                    return await call_generations(session, with_ref=True)
                return await call_generations(session, with_ref=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[emoji_sticker] 图片生成异常: {e}")
            return None

    def _get_ref_images(self) -> List[bytes]:
        """获取人设参考图: 优先本插件面板上传, 其次 gpt_image_2_simple 的 persona 目录。"""
        imgs: List[bytes] = []
        data_root = Path(__file__).resolve().parent.parent.parent
        roots = [
            Path.cwd(),
            data_root,
            data_root / "plugin_data",
            data_root / "plugins",
        ]
        plugin_data_dir = data_root / "plugin_data"
        if plugin_data_dir.is_dir():
            for sub in plugin_data_dir.iterdir():
                if sub.is_dir():
                    roots.append(sub)
        for item in self.ref_upload:
            try:
                p = Path(str(item))
                candidates = [p] if p.is_absolute() else [root / p for root in roots]
                for cand in candidates:
                    try:
                        cand = cand.resolve()
                    except Exception:
                        pass
                    if cand.is_file() and cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        imgs.append(cand.read_bytes())
                        logger.info(f"[emoji_sticker] 命中参考图: {cand}")
                        break
            except Exception as e:
                logger.warning(f"[emoji_sticker] 参考图加载失败: {e}")
        # 兜底: gpt_image_2_simple 的人设参考图目录
        if not imgs:
            plugins_dir = data_root / "plugins"
            persona_dir = plugins_dir / "astrbot_plugin_gpt_image_2_simple" / "data" / "persona"
            if persona_dir.is_dir():
                for f in sorted(persona_dir.iterdir()):
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        try:
                            imgs.append(f.read_bytes())
                            logger.info(f"[emoji_sticker] 使用 gpt_image_2_simple 人设参考图: {f}")
                            break
                        except Exception:
                            continue
        return imgs

    async def _optimize_prompt_via_llm(self, prompt: str, error_info: str) -> Optional[str]:
        """用 LLM 把被拒绝/失败的提示词改写为更安全合规的版本, 失败返回 None。"""
        if not self.auto_retry:
            return None
        try:
            prov = self.context.get_using_provider()
        except Exception as e:
            logger.debug(f"[emoji_sticker] get_using_provider failed: {e}")
            return None
        if prov is None:
            return None
        sys_prompt = (
            "你是一个图片提示词改写助手。用户的绘图提示词在调用图片生成API时被内容审核拒绝或生成失败。\n"
            "请将提示词改写为一条更安全、更含蓄、更容易通过审核的版本。\n"
            "要求:\n"
            "1. 保留原画面的核心意境、构图、动作的大方向。\n"
            "2. 用更安全、更礼貌、更含蓄的描述替换可能触发审核的表述。\n"
            "3. 输出为一条干净的提示词(中英文均可), 不要任何解释、前缀、引号或编号。\n"
            "4. 只输出一行。"
        )
        user = f"原始提示词:\n{prompt}\n\n图片API返回的错误:\n{error_info}"
        try:
            resp = await prov.text_chat(prompt=user, system_prompt=sys_prompt)
            new_prompt = (resp.completion_text or "").strip() if resp else ""
            if not new_prompt:
                return None
            logger.info(f"[emoji_sticker] 提示词已优化: {new_prompt[:160]}")
            return new_prompt[:500]
        except Exception as e:
            logger.warning(f"[emoji_sticker] 提示词优化失败: {e}")
            return None

    async def _generate_with_retry(self, prompt: str, ref_images: Optional[List[bytes]] = None) -> Optional[bytes]:
        """生成图片; 失败时用 LLM 优化提示词重试, 直到成功或达到次数上限。"""
        current = prompt
        result: Optional[bytes] = None
        for attempt in range(1, self.max_retries + 1):
            result = await self._generate_image(current, ref_images)
            if result:
                return result
            if not self.auto_retry or attempt >= self.max_retries:
                return result
            error_info = f"第 {attempt} 次调用图片 API 失败(空返回或被拒绝)"
            logger.warning(f"[emoji_sticker] 第 {attempt} 次生成失败, 正在优化提示词重试...")
            new_prompt = await self._optimize_prompt_via_llm(current, error_info)
            if not new_prompt or new_prompt == current:
                break
            current = new_prompt
        return result

    async def _send_sticker(self, event: AstrMessageEvent, scene: str, prompt_hint: str = "") -> bool:
        """生成并发送一张表情包, 成功返回 True"""
        if not self._can_send(scene):
            return False
        cfg = self.scenes.get(scene, {})
        scene_prompt = cfg.get("prompt", f"二次元{scene}表情包, 透明背景, 高清")
        # 纯表情动作描述(不含人物形象, 避免和参考图冲突)
        scene_action = cfg.get("action", "") or scene_prompt
        if prompt_hint:
            scene_action = f"{scene_action}, {prompt_hint}"

        if self.use_persona:
            # 参考图表情包: 人物形象完全由参考图决定, prompt 只描述表情动作
            parts = []
            if self.persona_desc:
                parts.append(self.persona_desc)
            if self.persona_style:
                parts.append(self.persona_style)
            parts.append(f"表情动作: {scene_action}")
            parts.append("表情包风格, 可爱, 生动, 特写")
            full_prompt = f"{self.identity_lock}, " + ", ".join(parts)
            refs = self._get_ref_images()
            img = await self._generate_with_retry(full_prompt, refs or None)
        else:
            full_prompt = f"{scene_prompt}, 适合表达「{scene}」情绪的可爱表情包"
            img = await self._generate_with_retry(full_prompt)
        if not img:
            return False
        if not self._can_send(scene):
            return False
        self._record_send(scene)
        try:
            await event.send(event.chain_result([Image.fromBytes(img)]))
            logger.info(f"[emoji_sticker] 已发送「{scene}」人设表情包")
            return True
        except Exception as e:
            logger.error(f"[emoji_sticker] 发送图片失败: {e}")
            return False

    # ------------------------------------------------------------------
    # LLM 主动调用工具
    # ------------------------------------------------------------------
    @filter.llm_tool(name="send_sticker")
    async def send_sticker_tool(self, event: AstrMessageEvent, scene: str) -> str:
        """当你此刻想用一张表情包图片来表达强烈情绪时调用此工具, 比纯文字更生动可爱。
        典型场景: 撒娇、想你、晚安早安、开心、生气无语、安慰、庆祝。
        注意: 调用后, 如果不是 send_mode=append 模式, 请不要再输出重复含义的长文字, 简单说一句即可或什么都不说。

        Args:
            scene (str): 情绪场景, 必须是以下之一: 撒娇, 晚安, 开心, 生气, 安慰, 庆祝
        """
        scene = (scene or "").strip()
        if scene not in self.scenes:
            return f"未知场景「{scene}」, 可用场景: {'、'.join(self.scenes.keys())}, 请改用文字回复。"
        self._check_daily_reset()
        if not self._can_send(scene):
            return f"「{scene}」表情包今日配额已用完或仍在冷却中, 请直接使用文字回复。"
        ok = await self._send_sticker(event, scene)
        if ok:
            return f"已发送「{scene}」表情包。" if self.send_mode == "append" else "已发送表情包, 本条无需再输出文字。"
        return "表情包生成失败, 请使用文字回复。"

    # ------------------------------------------------------------------
    # 关键词监听(不阻塞 LLM 正常回复)
    # ------------------------------------------------------------------
    @filter.on_llm_response(priority=10)
    async def on_llm_response(self, event: AstrMessageEvent, resp: Any):
        self._msg_since_last += 1
        self._check_daily_reset()

        text = self._extract_text(resp)
        if not text or len(text) < 2:
            return

        # 防重复处理
        sig = text[:120]
        if sig == self._last_text_signature:
            return
        self._last_text_signature = sig

        # 1) 显式标记 [STICKER:场景]  -> 必定触发(受额度限制)
        m = self._marker_re.search(text)
        if m:
            scene = m.group(1)
            if scene in self.scenes:
                self._schedule_send(event, scene, force=True)
                return
            return

        # 2) 关键词检测 -> 概率触发
        scene = self._detect_scene(text)
        if scene and self._should_trigger(scene):
            self._schedule_send(event, scene, force=False)

    def _schedule_send(self, event: AstrMessageEvent, scene: str, force: bool):
        task = asyncio.create_task(self._background_send(event, scene))
        self._pending_tasks.append(task)
        task.add_done_callback(lambda t: self._pending_tasks.remove(t) if t in self._pending_tasks else None)

    async def _background_send(self, event: AstrMessageEvent, scene: str):
        """后台异步发送, 不阻塞 LLM 回复速度"""
        try:
            await self._send_sticker(event, scene)
        except Exception as e:
            logger.error(f"[emoji_sticker] 后台发送异常: {e}")

    # ------------------------------------------------------------------
    # 提取 LLM 回复文本(兼容多种 resp 类型)
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_text(resp: Any) -> str:
        if resp is None:
            return ""
        if isinstance(resp, str):
            return resp
        if isinstance(resp, list):
            buf = []
            for seg in resp:
                if isinstance(seg, str):
                    buf.append(seg)
                elif isinstance(seg, dict):
                    if seg.get("type") in ("plain", "text"):
                        buf.append(str(seg.get("data", "") or seg.get("text", "")))
                else:
                    v = getattr(seg, "text", None) or getattr(seg, "content", None)
                    if v:
                        buf.append(str(v))
            return "".join(buf)
        for attr in ("completion_text", "raw_completion", "text", "content", "message"):
            v = getattr(resp, attr, None)
            if v:
                if isinstance(v, str):
                    return v
                if isinstance(v, list):
                    return EmojiStickerPlugin._extract_text(v)
        return ""

    async def terminate(self):
        for t in self._pending_tasks:
            t.cancel()
        for t in self._pending_tasks:
            try:
                await t
            except Exception:
                pass
        self._pending_tasks.clear()
