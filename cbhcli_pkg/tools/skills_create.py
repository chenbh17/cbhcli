"""Skills创建工具 - 允许Agent自主创建技能"""
import os
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class SkillsCreateTool(BaseTool):
    """技能创建工具
    
    允许 Agent 根据用户需求，在自己的工作空间下创建技能。
    包括编写 skills.md 提示词和可选的 script/ 下可执行脚本。
    """
    
    def __init__(self, app):
        """
        Args:
            app: CBHCLIApp 实例，用于获取当前 agent 的 skill_manager
        """
        self._app = app
    
    @property
    def name(self) -> str:
        return "skills_create"
    
    @property
    def description(self) -> str:
        return (
            "创建新技能。在当前Agent工作空间的skills文件夹下创建技能。"
            "技能包含skills.md提示词文件和可选的script/可执行脚本。"
            "创建后技能自动注册，用户可通过/skills use激活。"
        )
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "技能名称（英文，用作文件夹名，如 code-review, data-analysis）"
                },
                "prompt_content": {
                    "type": "string",
                    "description": "skills.md 的内容，即该技能的提示词，描述技能的功能、使用方法、注意事项等"
                },
                "scripts": {
                    "type": "object",
                    "description": "可选。脚本文件字典，key为文件名，value为脚本内容。如 {\"run.sh\": \"#!/bin/bash\\necho hello\"}。无脚本时传空对象{}或不传。",
                    "additionalProperties": {
                        "type": "string"
                    }
                }
            },
            "required": ["skill_name", "prompt_content"]
        }
    
    def execute(self, skill_name: str, prompt_content: str, 
                scripts: dict = None, **kwargs) -> ToolResult:
        """创建技能
        
        Args:
            skill_name: 技能名称
            prompt_content: skills.md 内容
            scripts: 脚本文件字典 {filename: content}
            
        Returns:
            ToolResult
        """
        # 获取当前 agent 的 skill_manager
        skill_manager = getattr(self._app, 'skill_manager', None)
        if not skill_manager:
            return ToolResult(
                success=False,
                output="",
                error="技能管理器未初始化，请确保已加载Agent"
            )
        
        # 校验技能名称
        if not skill_name or not skill_name.strip():
            return ToolResult(
                success=False,
                output="",
                error="技能名称不能为空"
            )
        
        # 清理名称（只保留字母、数字、连字符、下划线）
        clean_name = ''.join(
            c for c in skill_name.strip() 
            if c.isalnum() or c in '-_'
        )
        if not clean_name:
            return ToolResult(
                success=False,
                output="",
                error="技能名称只能包含字母、数字、连字符和下划线"
            )
        
        try:
            skill = skill_manager.create_skill(
                name=clean_name,
                prompt_content=prompt_content,
                scripts=scripts or {}
            )
            
            # 构建返回信息
            output_parts = [
                f"技能 '{clean_name}' 创建成功！",
                f"路径: {skill.base_dir}",
                f"提示词: {skill.base_dir / 'skills.md'}",
            ]
            
            if skill.has_scripts:
                output_parts.append(f"脚本目录: {skill.script_dir}")
                output_parts.append(f"脚本文件: {', '.join(skill.list_scripts())}")
            else:
                output_parts.append("脚本: 无")
            
            output_parts.append(f"\n用户可通过 /skills use 激活此技能")
            
            return ToolResult(
                success=True,
                output="\n".join(output_parts)
            )
            
        except ValueError as e:
            return ToolResult(
                success=False,
                output="",
                error=str(e)
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"创建技能失败: {str(e)}"
            )
