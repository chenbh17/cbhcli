"""图片识别工具 - 调用视觉模型识别图片内容"""
import base64
import mimetypes
from pathlib import Path
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class ImageTool(BaseTool):
    """图片识别工具

    当主模型不支持视觉功能时，通过此工具调用已配置的视觉模型识别图片内容。
    支持同时识别多张图片，返回视觉模型的识别结果给主模型。
    """

    def __init__(self, app):
        """
        Args:
            app: CBHCLIApp 实例（用于获取全局配置中的视觉模型）
        """
        self._app = app

    @property
    def name(self) -> str:
        return "image"

    @property
    def description(self) -> str:
        return (
            "使用视觉模型识别图片内容并返回识别结果。"
            "当需要分析、识别、描述图片时调用此工具。"
            "支持同时传入多张图片路径。"
            "工具会自动选择已配置的视觉模型进行识别。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要识别的图片路径列表，支持绝对路径和~路径"
                },
                "prompt": {
                    "type": "string",
                    "description": "对图片的识别需求描述，例如'描述这张图片的内容'或'提取图片中的文字'"
                }
            },
            "required": ["image_paths", "prompt"]
        }

    def execute(self, image_paths: list, prompt: str, **kwargs) -> ToolResult:
        """执行图片识别

        Args:
            image_paths: 图片文件路径列表
            prompt: 识别需求描述

        Returns:
            ToolResult: 视觉模型的识别结果
        """
        if not image_paths:
            return ToolResult(
                success=False,
                output="",
                error="未提供图片路径"
            )

        # 查找已配置的视觉模型（按优先级排序的列表）
        vision_models = self._find_vision_models()
        if not vision_models:
            return ToolResult(
                success=False,
                output="",
                error=(
                    "未找到已配置的视觉模型。"
                    "请先使用 /model add 添加一个支持视觉功能的模型"
                    "（添加时选择支持视觉为 y），然后重试。"
                )
            )

        # 加载图片为 base64
        base64_images = []
        loaded_paths = []
        for path in image_paths:
            expanded = Path(path).expanduser()
            if not expanded.exists():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"图片文件不存在: {path}"
                )
            if not expanded.is_file():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"不是文件: {path}"
                )

            # 检测图片类型
            mime_type, _ = mimetypes.guess_type(str(expanded))
            if not mime_type or not mime_type.startswith('image/'):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"不是图片文件: {path}"
                )

            try:
                with open(expanded, 'rb') as f:
                    image_data = f.read()
                    base64_data = base64.b64encode(image_data).decode('utf-8')
                    base64_images.append((base64_data, mime_type))
                    loaded_paths.append(str(expanded))
            except Exception as e:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"加载图片失败 {path}: {str(e)}"
                )

        # 依次尝试视觉模型，直到成功或全部失败
        last_error = None
        used_model_name = None
        result = None

        for i, model_config in enumerate(vision_models):
            model_name = model_config.get('name', '?')
            try:
                result = self._call_vision_model(
                    model_config, base64_images, prompt
                )
                used_model_name = model_name
                break
            except Exception as e:
                last_error = e
                remaining = len(vision_models) - i - 1
                if remaining > 0:
                    print(f"\n⚠️  视觉模型 '{model_name}' 调用失败: {e}")
                    print(f"🔄 尝试下一个备用视觉模型（剩余 {remaining} 个）...")
                continue

        if result is None:
            error_msg = f"所有视觉模型均调用失败"
            if last_error:
                error_msg += f"，最后错误: {last_error}"
            return ToolResult(
                success=False,
                output="",
                error=error_msg
            )

        # 构建输出（返回给AI的完整信息）
        output_lines = [
            f"📷 已使用视觉模型 '{used_model_name}' 识别 {len(base64_images)} 张图片",
            f"📋 识别需求: {prompt}",
            f"🖼️  图片列表: {', '.join(loaded_paths)}",
            "",
            "--- 识别结果 ---",
            result,
            "--- 结束 ---"
        ]

        # 终端显示：只显示识别结果摘要（参数已在工具调用确认前显示）
        result_preview = result[:200] + "..." if len(result) > 200 else result
        display_lines = [
            f"📷 '{used_model_name}' 识别完成",
            f"   {result_preview}",
        ]

        return ToolResult(
            success=True,
            output="\n".join(output_lines),
            display_output="\n".join(display_lines)
        )

    def _find_vision_models(self) -> list:
        """从全局配置中查找支持视觉的模型，按优先级排序

        优先级顺序：
        1. 当前Agent使用的模型（如果支持视觉）
        2. /fallback vision 配置的备用视觉模型（按顺序）
        3. 其他已配置的支持视觉的模型

        Returns:
            模型配置字典列表（按优先级排序），空列表表示无可用视觉模型
        """
        from cbhcli_pkg.config.global_config import GlobalConfig
        config = GlobalConfig()
        models = config.get_models()

        if not models:
            return []

        result = []
        seen_names = set()

        # 1. 优先：当前Agent使用的模型是否支持视觉
        if self._app and self._app.llm_client:
            if getattr(self._app.llm_client, 'supports_vision', False):
                current_name = self._app.llm_client.model_name
                for m in models:
                    if m.get('name') == current_name:
                        result.append(m)
                        seen_names.add(current_name)
                        break
                else:
                    # 找不到配置但支持视觉，用当前client信息构建
                    result.append({
                        'name': current_name,
                        'apiKey': self._app.llm_client.api_key,
                        'url': self._app.llm_client.base_url,
                        'model': self._app.llm_client.model_name,
                        'vision': True
                    })
                    seen_names.add(current_name)

        # 2. 备用视觉模型（按 /fallback vision 配置的顺序）
        fallback_vision = config.get_fallback_vision_models()
        for name in fallback_vision:
            if name in seen_names:
                continue
            model = config.get_model(name)
            if model and model.get('vision', False):
                result.append(model)
                seen_names.add(name)

        # 3. 其他已配置的支持视觉的模型
        for m in models:
            name = m.get('name', '')
            if name in seen_names:
                continue
            if m.get('vision', False):
                result.append(m)
                seen_names.add(name)

        return result

    def _call_vision_model(self, model_config: dict, images: list, prompt: str) -> str:
        """调用视觉模型识别图片

        Args:
            model_config: 模型配置字典
            images: [(base64_data, mime_type), ...] 图片列表
            prompt: 识别需求

        Returns:
            视觉模型的文本响应
        """
        from cbhcli_pkg.core.model import LLMClient

        # 创建视觉模型的 LLM 客户端
        client = LLMClient(model_config)

        # 构建多模态消息
        content_parts = [{"type": "text", "text": prompt}]
        for base64_data, mime_type in images:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{base64_data}"}
            })

        messages = [
            {"role": "user", "content": content_parts}
        ]

        # 使用非流式调用获取完整响应
        response = client.chat(messages, temperature=0.1)
        return response