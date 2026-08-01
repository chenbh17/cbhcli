"""QQ Bot REST API 客户端

封装 QQ 开放平台的 HTTP REST API，用于：
- 发送私聊消息 (C2C)
- 发送群聊消息
- 发送频道消息
- 上传媒体文件
- 获取用户信息
- 获取群信息

API 文档: https://bot.q.qq.com/wiki/develop/api-v2/
"""
import json
import uuid
import time
import logging
import requests
from typing import Optional, Any
from urllib.parse import urlencode

from cbhcli_pkg.qqbot.qqbot_config import QQBotConfig

logger = logging.getLogger(__name__)

# API 基础地址
API_BASE = "https://api.sgroup.qq.com"
ACCESS_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

# HTTP 超时配置
HTTP_TIMEOUT = 30  # HTTP 请求超时（从10提升到30秒）
HTTP_RETRY_COUNT = 3  # HTTP 请求重试次数

# 消息类型
MSG_TYPE_TEXT = 0        # 文本消息
MSG_TYPE_MARKDOWN = 2    # Markdown 消息
MSG_TYPE_ARK = 3         # Ark 模板消息
MSG_TYPE_EMBED = 4       # Embed 消息
MSG_TYPE_MEDIA = 7       # 富媒体消息


class QQBotAPIClient:
    """QQ Bot REST API 客户端"""

    def __init__(self, config: QQBotConfig):
        """
        Args:
            config: QQ Bot 配置
        """
        self.config = config
        self._session = requests.Session()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-Union-Appid": config.appId,
        })
        # 首次获取 token
        self._ensure_access_token()

    @property
    def api_base(self) -> str:
        """根据沙箱/正式环境返回正确的 API 地址"""
        if self.config.sandbox:
            return "https://sandbox.api.sgroup.qq.com"
        return "https://api.sgroup.qq.com"

    def _get_access_token(self) -> bool:
        """获取 QQ Bot access_token（带重试）"""
        for attempt in range(HTTP_RETRY_COUNT):
            try:
                resp = requests.post(
                    ACCESS_TOKEN_URL,
                    json={
                        "appId": self.config.appId,
                        "clientSecret": self.config.appSecret,
                    },
                    timeout=HTTP_TIMEOUT
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._access_token = data.get('access_token', '')
                    expires_in = int(data.get('expires_in', 7200))
                    self._token_expires_at = time.time() + expires_in - 60
                    logger.info(f"API: access_token 获取成功 (有效期 {expires_in}s)")
                    return True
                else:
                    logger.error(f"API: 获取 access_token 失败: HTTP {resp.status_code}")
                    if attempt < HTTP_RETRY_COUNT - 1:
                        time.sleep(2 * (attempt + 1))
                    continue
            except requests.RequestException as e:
                logger.error(f"API: 获取 access_token 请求失败 (第{attempt+1}次): {e}")
                if attempt < HTTP_RETRY_COUNT - 1:
                    time.sleep(2 * (attempt + 1))
        return False

    def _ensure_access_token(self) -> bool:
        """确保 access_token 有效"""
        if self._access_token and time.time() < self._token_expires_at:
            return True
        return self._get_access_token()

    def _auth_headers(self, extra: dict = None) -> dict:
        """获取带认证的请求头（自动刷新 token）"""
        self._ensure_access_token()
        headers = {
            "Authorization": f"QQBot {self._access_token or ''}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    # ════════════════════════════════════════════════
    # 消息发送
    # ════════════════════════════════════════════════

    def send_c2c_message(
        self,
        openid: str,
        content: str,
        msg_type: int = MSG_TYPE_TEXT,
        msg_id: Optional[str] = None,
        file_info: Optional[str] = None,
        media_info: Optional[dict] = None,
    ) -> dict:
        """发送私聊消息（C2C）

        Args:
            openid: 接收者 openid
            content: 消息内容（文本/Markdown）
            msg_type: 消息类型（0=文本, 2=Markdown, 3=Ark, 7=媒体）
            msg_id: 被动回复时传原始消息 ID；主动消息传 None（不传 msg_id/event_id）
            file_info: 媒体消息的 file_info 字符串（msg_type=7 时使用）
            media_info: 富媒体信息 dict（msg_type=7 时使用，优先级高于 file_info）

        Returns:
            API 响应 JSON
        """
        url = f"{self.api_base}/v2/users/{openid}/messages"

        payload = {
            "msg_type": msg_type,
        }
        # 正式环境消息发送规则（QQ官方文档）：
        # - 被动回复：传 msg_id = 原始消息 ID（60分钟内有效，最多回复5次）
        # - 主动消息：不传 msg_id / event_id（每月每用户最多4条）
        # msg_id 和 event_id 都是可选字段，主动消息直接不传即可
        if not self.config.sandbox:
            if msg_id is not None:
                payload["msg_id"] = msg_id

        if msg_type == MSG_TYPE_MEDIA:
            # 媒体消息：使用 media 字段
            if media_info:
                payload["media"] = media_info
            elif file_info:
                payload["media"] = {"file_info": file_info}
            elif content:
                payload["media"] = {"file_info": content}
        elif msg_type == MSG_TYPE_MARKDOWN:
            # Markdown 消息：只发 markdown 字段，不发 content（否则QQ会发两条消息）
            payload["markdown"] = {"content": content}
        else:
            payload["content"] = content

        return self._post(url, payload)

    def send_group_message(
        self,
        group_openid: str,
        content: str,
        msg_type: int = MSG_TYPE_TEXT,
        msg_id: Optional[str] = None,
        file_info: Optional[str] = None,
        media_info: Optional[dict] = None,
    ) -> dict:
        """发送群聊消息

        Args:
            group_openid: 群组 openid
            content: 消息内容
            msg_type: 消息类型
            msg_id: 被动回复时传原始消息 ID；主动消息传 None（不传 msg_id/event_id）
            file_info: 媒体消息的 file_info 字符串
            media_info: 富媒体信息 dict
        """
        url = f"{self.api_base}/v2/groups/{group_openid}/messages"

        payload = {"msg_type": msg_type}
        # 正式环境消息发送规则（QQ官方文档）：
        # - 被动回复：传 msg_id = 原始消息 ID（5分钟内有效，最多回复5次）
        # - 主动消息：不传 msg_id / event_id（每月每群最多4条）
        # msg_id 和 event_id 都是可选字段，主动消息直接不传即可
        if not self.config.sandbox:
            if msg_id is not None:
                payload["msg_id"] = msg_id

        if msg_type == MSG_TYPE_MEDIA:
            if media_info:
                payload["media"] = media_info
            elif file_info:
                payload["media"] = {"file_info": file_info}
            elif content:
                payload["media"] = {"file_info": content}
        elif msg_type == MSG_TYPE_MARKDOWN:
            # Markdown 消息：只发 markdown 字段，不发 content（否则QQ会发两条消息）
            payload["markdown"] = {"content": content}
        else:
            payload["content"] = content

        return self._post(url, payload)

    def send_channel_message(
        self,
        channel_id: str,
        content: str,
        msg_type: int = MSG_TYPE_TEXT,
        msg_id: Optional[str] = None,
    ) -> dict:
        """发送频道消息

        Args:
            channel_id: 子频道 ID
            content: 消息内容
            msg_type: 消息类型
            msg_id: 消息 ID

        Returns:
            API 响应 JSON
        """
        url = f"{self.api_base}/v2/channels/{channel_id}/messages"

        if msg_id is None:
            msg_id = str(uuid.uuid4())

        payload = {
            "content": content,
            "msg_type": msg_type,
            "msg_id": msg_id,
        }

        return self._post(url, payload)

    def send_markdown_message(
        self,
        target_type: str,
        target_id: str,
        markdown_content: str,
    ) -> dict:
        """便捷方法：发送 Markdown 消息

        Args:
            target_type: "c2c" 或 "group" 或 "channel"
            target_id: 对应的 openid / group_openid / channel_id
            markdown_content: Markdown 格式内容

        Returns:
            API 响应 JSON
        """
        if target_type == "c2c":
            return self.send_c2c_message(target_id, markdown_content, msg_type=MSG_TYPE_MARKDOWN)
        elif target_type == "group":
            return self.send_group_message(target_id, markdown_content, msg_type=MSG_TYPE_MARKDOWN)
        elif target_type == "channel":
            return self.send_channel_message(target_id, markdown_content, msg_type=MSG_TYPE_MARKDOWN)
        else:
            return {"error": f"不支持的目标类型: {target_type}"}

    # ════════════════════════════════════════════════
    # 媒体上传
    # ════════════════════════════════════════════════

    def upload_media(
        self,
        file_path: str,
        file_type: int = 1,
        target_type: str = "c2c",
        target_id: str = "",
    ) -> dict:
        """上传媒体文件到 QQ 服务器

        QQ API 使用 JSON body 传 base64 编码的文件数据，
        不是 multipart form upload。

        Args:
            file_path: 本地文件路径
            file_type: 文件类型 (1=图片, 2=视频, 3=语音, 4=文件)
            target_type: "c2c" 或 "group"
            target_id: 目标用户 openid（c2c）或群 openid（group）

        Returns:
            包含 file_info 的响应 JSON
        """
        import base64 as _b64

        if target_type == "group" and target_id:
            url = f"{self.api_base}/v2/groups/{target_id}/files"
        elif target_id:
            url = f"{self.api_base}/v2/users/{target_id}/files"
        else:
            url = f"{self.api_base}/v2/users/{self.config.appId}/files"

        try:
            with open(file_path, 'rb') as f:
                file_data = _b64.b64encode(f.read()).decode('utf-8')

            headers = self._auth_headers()
            headers["X-Union-Appid"] = self.config.appId

            payload = {"file_type": file_type, "file_data": file_data}
            # file_type=4（文件）时支持 file_name 参数传递文件名
            if file_type == 4:
                fname = file_path.split('/')[-1]
                if fname:
                    payload["file_name"] = fname
            logger.info(f"上传文件: url={url}, file_type={file_type}, data_len={len(file_data)}")

            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=60
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"上传失败: url={url}, status={resp.status_code}, body={resp.text[:500]}")
                return {"error": f"上传失败(url={url}): HTTP {resp.status_code} {resp.text[:300]}"}
        except Exception as e:
            return {"error": f"上传异常: {e}"}

    def upload_and_send_media(
        self,
        target_type: str,
        target_id: str,
        file_path: str,
        file_type: int = 1,
    ) -> dict:
        """上传文件并发送媒体消息

        Args:
            target_type: "c2c" 或 "group"
            target_id: 接收者 ID
            file_path: 本地文件路径
            file_type: 文件类型 (1=图片, 2=视频, 3=语音, 4=文件)

        Returns:
            API 响应 JSON
        """
        upload_result = self.upload_media(file_path, file_type,
                                           target_type=target_type, target_id=target_id)
        if 'error' in upload_result:
            return upload_result

        file_info = upload_result.get('file_info', '')
        if not file_info:
            return {"error": "上传成功但未返回 file_info"}

        media_info = {"file_info": file_info}

        if target_type == "c2c":
            return self.send_c2c_message(target_id, "", msg_type=MSG_TYPE_MEDIA, media_info=media_info)
        elif target_type == "group":
            return self.send_group_message(target_id, "", msg_type=MSG_TYPE_MEDIA, media_info=media_info)
        else:
            return {"error": f"不支持的目标类型: {target_type}"}

    def send_file_message(
        self,
        target_type: str,
        target_id: str,
        file_path: str,
    ) -> dict:
        """发送文件消息（上传 + 发送一步完成）

        Args:
            target_type: "c2c" 或 "group"
            target_id: 接收者 ID
            file_path: 本地文件路径

        Returns:
            API 响应 JSON
        """
        # 根据扩展名判断文件类型
        ext = file_path.lower().split('.')[-1] if '.' in file_path else ''
        image_exts = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'}
        video_exts = {'mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv'}
        audio_exts = {'mp3', 'wav', 'aac', 'ogg', 'flac', 'silk', 'amr'}

        if ext in image_exts:
            file_type = 1  # 图片
        elif ext in video_exts:
            file_type = 2  # 视频
        elif ext in audio_exts:
            file_type = 3  # 语音
        else:
            file_type = 4  # 文件

        return self.upload_and_send_media(target_type, target_id, file_path, file_type)

    def send_media_message(
        self,
        target_type: str,
        target_id: str,
        file_info: str,
    ) -> dict:
        """发送媒体消息（已上传的文件）

        Args:
            target_type: "c2c" 或 "group"
            target_id: 接收者 ID
            file_info: 上传后返回的 file_info 字符串
        """
        media_info = {"file_info": file_info}
        if target_type == "c2c":
            return self.send_c2c_message(target_id, "", msg_type=MSG_TYPE_MEDIA, media_info=media_info)
        elif target_type == "group":
            return self.send_group_message(target_id, "", msg_type=MSG_TYPE_MEDIA, media_info=media_info)
        else:
            return {"error": f"不支持的目标类型: {target_type}"}

    # ════════════════════════════════════════════════
    # 信息查询
    # ════════════════════════════════════════════════

    def get_user_info(self, openid: str) -> dict:
        """获取用户信息

        Args:
            openid: 用户 openid

        Returns:
            用户信息 JSON
        """
        url = f"{self.api_base}/v2/users/{openid}"
        return self._get(url)

    def get_group_info(self, group_openid: str) -> dict:
        """获取群信息

        Args:
            group_openid: 群 openid

        Returns:
            群信息 JSON
        """
        url = f"{self.api_base}/v2/groups/{group_openid}"
        return self._get(url)

    def get_me_info(self) -> dict:
        """获取 Bot 自身信息

        Returns:
            Bot 信息 JSON
        """
        url = f"{self.api_base}/v2/users/me"
        return self._get(url)

    # ════════════════════════════════════════════════
    # HTTP 方法封装
    # ════════════════════════════════════════════════

    def _get(self, url: str, params: dict = None) -> dict:
        """GET 请求"""
        try:
            resp = self._session.get(url, params=params, headers=self._auth_headers(), timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"GET {url} 失败: HTTP {resp.status_code} {resp.text}")
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        except requests.RequestException as e:
            logger.error(f"GET {url} 请求异常: {e}")
            return {"error": str(e)}

    def _post(self, url: str, data: dict) -> dict:
        """POST 请求"""
        try:
            resp = self._session.post(url, json=data, headers=self._auth_headers(), timeout=HTTP_TIMEOUT)
            if resp.status_code in (200, 201, 202):
                return resp.json() if resp.text else {"ok": True}
            else:
                logger.error(f"POST {url} 失败: HTTP {resp.status_code} {resp.text}")
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        except requests.RequestException as e:
            logger.error(f"POST {url} 请求异常: {e}")
            return {"error": str(e)}
