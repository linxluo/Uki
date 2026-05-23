"""
天气查询插件

支持查询全国城市的实时天气，数据来源 wttr.in（免费，无需 API Key）。
提供中英城市名查询，返回温度、天气状况、湿度、风力、体感温度等信息。
"""

import json
import urllib.request
import urllib.parse
from uki.plugin_manager import UkiPlugin


class WeatherPlugin(UkiPlugin):
    """实时天气查询插件。"""

    BASE_URL = "https://wttr.in"

    def on_load(self, agent=None):
        print(f"  [Weather] 插件 v{self.version} 就绪  ☀️")

    def get_tool_definitions(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": (
                        "查询指定城市的实时天气。支持中国城市名（如 北京、上海、广州、成都 等）。"
                        "返回当前温度、体感温度、天气状况、湿度、风力、风向、气压、能见度等信息。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "城市名称，中文或英文均可。例如：北京、上海、深圳、成都、杭州、西安",
                            },
                        },
                        "required": ["city"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_weather_forecast",
                    "description": (
                        "查询指定城市未来 2-3 天的天气预报。包含每日最高/最低温度、天气状况。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "城市名称，中文或英文均可。例如：北京、上海、广州",
                            },
                        },
                        "required": ["city"],
                    },
                },
            },
        ]

    def execute_tool(self, name: str, arguments: dict) -> str | None:
        if name == "get_weather":
            city = arguments.get("city", "")
            if not city:
                return "❌ 请提供城市名称。"
            return self._query_current(city)

        if name == "get_weather_forecast":
            city = arguments.get("city", "")
            if not city:
                return "❌ 请提供城市名称。"
            return self._query_forecast(city)

        return None

    def get_commands(self) -> list[tuple[str, str, callable]]:
        return [
            ("weather", "查询城市实时天气，用法：/weather <城市名>", self._cmd_weather),
        ]

    # ================================================================
    # 工具实现
    # ================================================================

    def _query_current(self, city: str) -> str:
        """查询实时天气（JSON API）。"""
        try:
            encoded_city = urllib.parse.quote(city)
            url = f"{self.BASE_URL}/{encoded_city}?format=j1"
            data = self._fetch_json(url)

            current = data.get("current_condition", [{}])[0]
            if not current:
                return f"⚠️ 未找到城市「{city}」的天气数据，请检查城市名是否正确。"

            # 基本信息
            temp_c = current.get("temp_C", "N/A")
            feels_like = current.get("FeelsLikeC", "N/A")
            desc = current.get("weatherDesc", [{}])[0].get("value", "未知")
            humidity = current.get("humidity", "N/A")
            wind_speed = current.get("windspeedKmph", "N/A")
            wind_dir = current.get("winddir16Point", "N/A")
            pressure = current.get("pressure", "N/A")
            visibility = current.get("visibility", "N/A")
            uv_index = current.get("uvIndex", "N/A")

            # 天气图标映射
            weather_icon = self._weather_icon(desc)

            return (
                f"📍 **{city}** 实时天气 {weather_icon}\n\n"
                f"🌡️  当前温度：**{temp_c}°C**（体感 {feels_like}°C）\n"
                f"☁️  天气状况：{desc}\n"
                f"💧 湿度：{humidity}%\n"
                f"🌬️  风力：{wind_speed} km/h（{wind_dir}）\n"
                f"🔽 气压：{pressure} hPa\n"
                f"👁️  能见度：{visibility} km\n"
                f"☀️  紫外线指数：{uv_index}"
            )

        except urllib.error.URLError as e:
            return f"❌ 网络请求失败：{e}\n请检查网络连接后重试。"
        except Exception as e:
            return f"❌ 查询失败：{e}"

    def _query_forecast(self, city: str) -> str:
        """查询未来几天天气预报。"""
        try:
            encoded_city = urllib.parse.quote(city)
            url = f"{self.BASE_URL}/{encoded_city}?format=j1"
            data = self._fetch_json(url)

            weather = data.get("weather", [])
            if not weather:
                return f"⚠️ 未找到城市「{city}」的天气预报数据。"

            lines = [f"📅 **{city}** 未来天气预报\n"]

            for day in weather[:3]:  # 最多显示 3 天
                date = day.get("date", "未知日期")
                max_temp = day.get("maxtempC", "N/A")
                min_temp = day.get("mintempC", "N/A")
                hourly = day.get("hourly", [])
                # 取中午时段的天气描述作为当日概况
                midday_desc = "未知"
                for h in hourly:
                    if h.get("weatherDesc"):
                        midday_desc = h["weatherDesc"][0]["value"]
                        break

                icon = self._weather_icon(midday_desc)
                lines.append(
                    f"  {date}  {icon}  {midday_desc}，"
                    f"🌡️ {min_temp}°C ~ {max_temp}°C"
                )

            return "\n".join(lines)

        except urllib.error.URLError as e:
            return f"❌ 网络请求失败：{e}"
        except Exception as e:
            return f"❌ 查询失败：{e}"

    def _fetch_json(self, url: str) -> dict:
        """发送 GET 请求并解析 JSON。"""
        req = urllib.request.Request(url, headers={"User-Agent": "UkiWeatherPlugin/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _weather_icon(desc: str) -> str:
        """根据天气描述返回 emoji 图标。"""
        desc_lower = desc.lower()
        if any(k in desc_lower for k in ["晴", "sunny", "clear"]):
            return "☀️"
        if any(k in desc_lower for k in ["多云", "cloudy", "partly"]):
            return "⛅"
        if any(k in desc_lower for k in ["阴", "overcast"]):
            return "☁️"
        if any(k in desc_lower for k in ["雨", "rain", "drizzle", "shower"]):
            return "🌧️"
        if any(k in desc_lower for k in ["雪", "snow"]):
            return "❄️"
        if any(k in desc_lower for k in ["雷", "thunder"]):
            return "⛈️"
        if any(k in desc_lower for k in ["雾", "mist", "fog", "haze"]):
            return "🌫️"
        return "🌈"

    # ================================================================
    # 命令处理
    # ================================================================

    def _cmd_weather(self, args: str) -> str:
        """处理 /weather 命令。"""
        city = args.strip()
        if not city:
            return "❌ 用法：/weather <城市名>\n例如：/weather 北京"
        return self._query_current(city)

    def on_unload(self):
        print(f"  [Weather] 插件已卸载")
