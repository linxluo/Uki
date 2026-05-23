"""
Uki 的插件管理器

对应 Claude Code 的 Plugin 系统：自动发现、加载、管理扩展模块。
一个插件就是一个自包含目录，可以给 Uki 添加新工具、新命令，甚至注入钩子。

设计原则：
  - 插件是独立目录，位于 plugins/ 下
  - 每个插件有一个 uki_plugin.json 清单文件
  - Python 插件通过标准接口与 Uki 交互
  - 加载失败不影响核心功能运行
"""

import json
import importlib.util
import sys
import subprocess
import zipfile
import shutil
from pathlib import Path
from typing import Any
from uki import display


class UkiPlugin:
    """
    插件基类。所有 Python 插件必须继承此类。

    插件可以扩展的能力：
      - get_tool_definitions() → 新增工具定义
      - execute_tool(name, args) → 执行工具并返回结果
      - get_commands() → 新增斜杠命令
    """

    def __init__(self, manifest: dict, plugin_dir: Path):
        self.manifest = manifest
        self.plugin_dir = plugin_dir
        self.name: str = manifest.get("name", plugin_dir.name)
        self.version: str = manifest.get("version", "0.0.0")
        self.description: str = manifest.get("description", "")

    def on_load(self, agent=None):
        """插件被加载时调用。agent 是 UkiAgent 实例的引用。"""
        pass

    def get_tool_definitions(self) -> list[dict]:
        """返回给 LLM 看的工具定义列表（OpenAI function calling 格式）。"""
        return []

    def execute_tool(self, name: str, arguments: dict) -> str | None:
        """
        执行此插件提供的工具。
        返回 None 表示不识别此工具名，由其他插件或内置工具处理。
        """
        return None

    def get_commands(self) -> list[tuple[str, str, callable]]:
        """
        返回命令列表。每个元素为 (命令名, 描述, 处理器函数)。
        命令名自动加上 / 前缀。
        """
        return []

    def on_unload(self):
        """插件被卸载时调用。"""
        pass


class PluginManager:
    """
    插件管理器：发现、加载、激活、卸载插件。

    使用方式：
        pm = PluginManager()
        pm.discover()        # 扫描 plugins/ 目录
        pm.load_all()        # 加载所有启用的插件
        pm.get_all_tools()   # 获取所有插件的工具定义
        pm.execute_tool()    # 尝试让插件执行工具
        pm.register_commands(registry)  # 注册插件命令到命令系统
    """

    def __init__(self, plugins_dir: str = "plugins"):
        self._plugins_dir = Path(plugins_dir)
        self._plugins: dict[str, UkiPlugin] = {}      # name → plugin instance
        self._modules: dict[str, Any] = {}             # name → imported module
        self._agent_ref = None

    def set_agent(self, agent):
        """设置 Agent 引用，供插件在 on_load 中使用。"""
        self._agent_ref = agent

    @property
    def loaded_plugins(self) -> list[str]:
        """返回已加载的插件名列表。"""
        return list(self._plugins.keys())

    # ================================================================
    # 发现（Discovery）
    # ================================================================

    def discover(self) -> list[dict]:
        """
        扫描 plugins/ 目录，发现所有有效插件（含已禁用的）。
        返回发现的插件清单列表。
        """
        if not self._plugins_dir.exists():
            return []

        found = []
        for entry in sorted(self._plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "uki_plugin.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["__dir__"] = str(entry)
                manifest["__manifest_path__"] = str(manifest_path)
                # 默认启用，除非显式设为 false
                manifest.setdefault("enabled", True)
                found.append(manifest)
            except (json.JSONDecodeError, Exception) as e:
                display.warning(f"  插件清单解析失败: {entry.name} ({e})")

        return found

    # ================================================================
    # 加载（Loading）
    # ================================================================

    def load_all(self) -> int:
        """
        加载所有发现的插件。
        返回成功加载的数量。
        """
        manifests = self.discover()
        if not manifests:
            display.info("未发现任何插件。")
            return 0

        loaded = 0
        for manifest in manifests:
            name = manifest.get("name", "unknown")

            # 跳过已加载的
            if name in self._plugins:
                continue

            # 跳过已禁用的
            if manifest.get("enabled") is False:
                display.info(f"  插件已禁用，跳过: {name}")
                continue

            plugin_type = manifest.get("type", "python")
            if plugin_type == "python":
                self._install_dependencies(manifest)
                if self._load_python_plugin(manifest):
                    loaded += 1
            elif plugin_type == "mcp":
                # MCP 类型插件：仅注册为 MCP 服务器配置来源
                # 当前 MVP 阶段暂不支持，后续课程深化
                display.info(f"  MCP 插件暂不支持: {name}")
            else:
                display.warning(f"  未知插件类型: {plugin_type} ({name})")

        return loaded

    def _load_python_plugin(self, manifest: dict) -> bool:
        """
        加载一个 Python 类型的插件。
        步骤：找到模块文件 → 动态导入 → 实例化 UkiPlugin 子类 → 调用 on_load
        """
        name = manifest.get("name", "unknown")
        plugin_dir = Path(manifest["__dir__"])

        # 1. 找到模块文件
        entry_file = manifest.get("entry")
        if entry_file:
            module_path = plugin_dir / entry_file
        else:
            # 默认尝试 plugin.py 或 __init__.py
            module_path = plugin_dir / "plugin.py"
            if not module_path.exists():
                module_path = plugin_dir / "__init__.py"

        if not module_path.exists():
            display.warning(f"  插件入口文件不存在: {module_path} ({name})")
            return False

        # 2. 动态导入模块
        try:
            module_name = f"uki_plugin_{name}"
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                display.warning(f"  无法解析插件模块: {name}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            self._modules[name] = module
        except Exception as e:
            display.warning(f"  插件导入失败: {name} ({e})")
            return False

        # 3. 找到 UkiPlugin 的子类并实例化
        plugin_instance = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, UkiPlugin)
                and attr is not UkiPlugin
            ):
                plugin_instance = attr(manifest, plugin_dir)
                break

        if plugin_instance is None:
            # 如果没找到 UkiPlugin 子类，尝试直接找任意以 Plugin 结尾的类
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and attr_name.endswith("Plugin") and hasattr(attr, "get_tool_definitions"):
                    plugin_instance = attr(manifest, plugin_dir)
                    break

        if plugin_instance is None:
            display.warning(f"  插件中未找到 UkiPlugin 子类: {name}")
            return False

        # 4. 调用生命周期钩子
        try:
            plugin_instance.on_load(self._agent_ref)
        except Exception as e:
            display.warning(f"  插件 on_load 失败: {name} ({e})")
            # 不阻止加载，工具和命令可能仍然可用

        self._plugins[name] = plugin_instance
        tool_count = len(plugin_instance.get_tool_definitions())
        cmd_count = len(plugin_instance.get_commands())
        display.success(f"  插件已加载: {name} v{plugin_instance.version} ({tool_count} 工具, {cmd_count} 命令)")
        return True

    # ================================================================
    # 工具接口
    # ================================================================

    def get_all_tool_definitions(self) -> list[dict]:
        """获取所有已加载插件的工具定义（合并列表）。"""
        all_tools = []
        for plugin in self._plugins.values():
            try:
                tools = plugin.get_tool_definitions()
                all_tools.extend(tools)
            except Exception as e:
                display.warning(f"  获取插件工具定义失败: {plugin.name} ({e})")
        return all_tools

    def execute_tool(self, name: str, arguments: dict) -> str | None:
        """
        尝试让所有插件执行指定工具。
        第一个返回非 None 结果的插件胜出。
        返回 None 表示没有插件处理此工具。
        """
        for plugin in self._plugins.values():
            try:
                result = plugin.execute_tool(name, arguments)
                if result is not None:
                    return result
            except Exception as e:
                display.warning(f"  插件工具执行异常: {plugin.name}.{name} ({e})")
        return None

    # ================================================================
    # 命令注册
    # ================================================================

    def register_commands(self, registry):
        """
        将所有插件的命令注册到给定的 CommandRegistry。
        registry 是 CommandRegistry 实例。
        """
        for plugin in self._plugins.values():
            try:
                for name, desc, handler in plugin.get_commands():
                    registry.register(f"/{name}", desc, handler)
            except Exception as e:
                display.warning(f"  注册插件命令失败: {plugin.name} ({e})")

    # ================================================================
    # Zip 安装（拖拽即用）
    # ================================================================

    def install_from_zip(self, zip_path: str) -> bool:
        """
        从一个 zip 文件安装插件：解压到 plugins/ 目录，装依赖，立即加载。
        zip 根目录必须包含 uki_plugin.json。
        """
        zip_file = Path(zip_path).resolve()
        if not zip_file.exists():
            display.warning(f"  Zip 文件不存在: {zip_path}")
            return False

        if not zip_file.suffix.lower() == ".zip":
            display.warning(f"  不是 zip 文件: {zip_path}")
            return False

        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                # 检查根目录是否包含 uki_plugin.json
                has_manifest = any(
                    name.endswith("uki_plugin.json") and "/" not in name.strip("/") 
                    for name in zf.namelist()
                )
                if not has_manifest:
                    # 可能在子目录里
                    candidates = [n for n in zf.namelist() if n.endswith("uki_plugin.json")]
                    if not candidates:
                        display.warning("  Zip 中未找到 uki_plugin.json，不是一个有效的插件包")
                        return False
                    # 用第一个找到的，取其父目录
                    root_dir = Path(candidates[0]).parent
                else:
                    root_dir = Path(".")

                # 解压到 plugins/ 目录
                dest = self._plugins_dir
                dest.mkdir(parents=True, exist_ok=True)

                # 解压
                zf.extractall(dest)
                display.success(f"  插件已解压到: {dest}")

        except zipfile.BadZipFile:
            display.warning(f"  无效的 zip 文件: {zip_path}")
            return False
        except Exception as e:
            display.warning(f"  解压失败: {e}")
            return False

        # 重新发现并加载新插件
        manifests = self.discover()
        loaded = 0
        for m in manifests:
            name = m.get("name", "unknown")
            if name not in self._plugins:
                self._install_dependencies(m)
                if self._load_python_plugin(m):
                    loaded += 1

        if loaded > 0:
            display.success(f"  插件安装完成，已加载 {loaded} 个新插件")
            return True
        else:
            display.info("  解压完成但未发现新插件，请检查 zip 结构")
            return False

    # ================================================================
    # 依赖安装
    # ================================================================

    def _install_dependencies(self, manifest: dict):
        """
        自动安装插件声明的依赖。
        使用 pip install，静默安装（不输出到终端）。
        """
        deps = manifest.get("dependencies", [])
        if not deps:
            return

        name = manifest.get("name", "unknown")
        missing = []
        for dep in deps:
            # 检查包是否已安装
            try:
                __import__(dep.replace("-", "_").split("[")[0].split(">")[0].split("<")[0].split("=")[0].split("~")[0].split("!")[0].strip())
            except ImportError:
                missing.append(dep)

        if not missing:
            return

        display.info(f"  正在安装插件依赖: {', '.join(missing)}")
        try:
            # 优先用清华镜像，失败则回退官方源
            for index_url in [
                "https://pypi.tuna.tsinghua.edu.cn/simple",
                "https://pypi.org/simple",
            ]:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q",
                     "-i", index_url,
                     "--trusted-host", index_url.split("//")[1].split("/")[0],
                     *missing],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    display.success(f"  依赖安装完成: {', '.join(missing)}")
                    return

            display.warning(f"  部分依赖安装失败: {', '.join(missing)}")
            if result.stderr:
                # 只显示最后一行错误
                err_lines = result.stderr.strip().split("\n")
                display.warning(f"  {err_lines[-1][:200]}")
        except Exception as e:
            display.warning(f"  依赖安装异常: {e}")

    # ================================================================
    # 插件卸载与状态
    # ================================================================

    def unload_all(self):
        """卸载所有已加载的插件。"""
        for name in list(self._plugins.keys()):
            self._unload_plugin(name)

    def _unload_plugin(self, name: str):
        """卸载单个插件。"""
        plugin = self._plugins.get(name)
        if plugin is None:
            return
        try:
            plugin.on_unload()
        except Exception as e:
            display.warning(f"  插件 on_unload 异常: {name} ({e})")
        del self._plugins[name]
        if name in self._modules:
            module_name = f"uki_plugin_{name}"
            sys.modules.pop(module_name, None)
            del self._modules[name]

    # ================================================================
    # 启用/禁用
    # ================================================================

    def enable_plugin(self, name: str) -> bool:
        """启用指定插件：写入 manifest + 重新加载。"""
        if name in self._plugins:
            return True  # 已加载

        manifest_path = self._find_manifest(name)
        if not manifest_path:
            return False

        self._set_enabled_flag(manifest_path, True)

        # 重新发现并加载
        manifests = self.discover()
        for m in manifests:
            if m.get("name") == name and m.get("enabled") is not False:
                self._install_dependencies(m)
                return self._load_python_plugin(m)
        return False

    def disable_plugin(self, name: str) -> bool:
        """禁用指定插件：卸载 + 写入 manifest。"""
        self._unload_plugin(name)

        manifest_path = self._find_manifest(name)
        if not manifest_path:
            # 找不到 manifest 也返回 True，因为插件已从内存卸载
            return True

        self._set_enabled_flag(manifest_path, False)
        return True

    def uninstall_plugin(self, name: str) -> bool:
        """彻底删除插件：卸载 + 删除整个插件目录。"""
        self._unload_plugin(name)
        manifest_path = self._find_manifest(name)
        if not manifest_path:
            return False
        plugin_dir = manifest_path.parent
        try:
            shutil.rmtree(plugin_dir, ignore_errors=True)
            return not plugin_dir.exists()
        except Exception as e:
            display.warning(f"  删除插件目录失败: {e}")
            return False

    def _find_manifest(self, name: str) -> Path | None:
        """查找插件的 uki_plugin.json 文件路径。"""
        if not self._plugins_dir.exists():
            return None
        for entry in self._plugins_dir.iterdir():
            if not entry.is_dir():
                continue
            mp = entry / "uki_plugin.json"
            if mp.exists():
                try:
                    data = json.loads(mp.read_text(encoding="utf-8"))
                    if data.get("name") == name:
                        return mp
                except Exception:
                    continue
        return None

    def _set_enabled_flag(self, manifest_path: Path, enabled: bool):
        """写入 manifest 的 enabled 字段。"""
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["enabled"] = enabled
            manifest_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            display.warning(f"  写入 manifest 失败: {e}")

    def get_all_plugins_info(self) -> list[dict]:
        """
        返回所有已发现插件的结构化信息，供前端 UI 使用。
        包括已加载和已禁用的插件。
        """
        manifests = self.discover()
        result = []
        for m in manifests:
            name = m.get("name", "unknown")
            loaded = name in self._plugins
            plugin = self._plugins.get(name)
            result.append({
                "name": name,
                "version": m.get("version", "?"),
                "description": m.get("description", ""),
                "enabled": m.get("enabled", True),
                "loaded": loaded,
                "tool_count": len(plugin.get_tool_definitions()) if plugin else 0,
                "command_count": len(plugin.get_commands()) if plugin else 0,
            })
        return result

    # ================================================================
    # 状态查询
    # ================================================================

    def plugin_status(self) -> str:
        """返回所有插件的状态摘要。"""
        if not self._plugins:
            return "当前没有加载任何插件。\n\n将插件放入 plugins/ 目录（需含 uki_plugin.json）即可自动发现。"

        lines = [f"已加载 {len(self._plugins)} 个插件:"]
        for name, plugin in self._plugins.items():
            t_count = len(plugin.get_tool_definitions())
            c_count = len(plugin.get_commands())
            lines.append(f"  📦 {name} v{plugin.version}")
            lines.append(f"     {plugin.description}")
            lines.append(f"     {t_count} 工具, {c_count} 命令")
        return "\n".join(lines)
