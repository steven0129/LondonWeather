import ctypes
import json
import queue
import sys
import threading
import urllib.request
from datetime import datetime
import tkinter as tk

import pystray
from PIL import Image, ImageDraw

LAT = 51.5072
LON = -0.1276
API_URL = (
    "https://api.open-meteo.com/v1/forecast?"
    f"latitude={LAT}&longitude={LON}"
    "&hourly=temperature_2m,precipitation,rain,weather_code,wind_speed_10m"
    "&forecast_days=1&timezone=Europe%2FLondon"
)

WIDTH = 520
HEIGHT = 330
REFRESH_MS = 15 * 60 * 1000

WEATHER_LABELS = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm hail", 99: "Thunderstorm hail",
}

SINGLE_INSTANCE_MUTEX_NAME = "Global\\HermesLondonRainTrendWidgetV2"


def ensure_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return mutex


def make_tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 6, 56, 58), radius=14, fill=(16, 23, 34, 235), outline=(68, 199, 255, 255), width=3)
    d.line((20, 18, 31, 10, 44, 18), fill=(68, 199, 255, 255), width=4)
    d.ellipse((18, 24, 26, 34), fill=(68, 199, 255, 255))
    d.ellipse((30, 31, 38, 43), fill=(68, 199, 255, 255))
    d.ellipse((42, 23, 50, 34), fill=(185, 242, 124, 255))
    return img


class FloatingRainApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("London Weather")
        self.root.geometry(f"{WIDTH}x{HEIGHT}")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.84)
        self.root.overrideredirect(True)
        self.root.configure(bg="#101722")
        self.root.resizable(False, False)
        self.root.bind("<Escape>", lambda e: self.hide_to_tray())
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, bg="#101722", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.start_move)
        self.canvas.bind("<B1-Motion>", self.do_move)
        self.canvas.bind("<ButtonRelease-1>", self.handle_click)
        self.canvas.bind("<Motion>", self.show_hover_value)
        self.canvas.bind("<Leave>", self.clear_hover_value)
        self.root.bind("<Button-3>", lambda e: self.hide_to_tray())

        self._drag_x = 0
        self._drag_y = 0
        self.data = None
        self.error = None
        self.closing = False
        self.commands = queue.Queue()
        self.tray_icon = None
        self.chart_state = None

        self.start_tray_icon()
        self.position_bottom_right()
        self.draw_loading()
        self.fetch_async()
        self.root.after(100, self.process_commands)
        self.root.after(REFRESH_MS, self.periodic_refresh)

    def start_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda icon, item: self.commands.put("show"), default=True),
            pystray.MenuItem("Quit", lambda icon, item: self.commands.put("quit")),
        )
        self.tray_icon = pystray.Icon("LondonRainTrend", make_tray_image(), "London rain trend — click to show", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def process_commands(self):
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                break
            if isinstance(cmd, tuple) and cmd[0] == "weather":
                self.apply_weather_result(cmd[1], cmd[2])
            elif cmd == "show":
                self.show_from_tray()
            elif cmd == "quit":
                self.quit_app()
                return
        if not self.closing:
            self.root.after(100, self.process_commands)

    def position_bottom_right(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, sw - WIDTH - 22)
        y = max(0, sh - HEIGHT - 66)
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def start_move(self, event):
        if event.y <= 34 and event.x >= WIDTH - 56:
            self._drag_x = 0
            self._drag_y = 0
            return
        self._drag_x = event.x
        self._drag_y = event.y

    def do_move(self, event):
        if self._drag_x == 0 and self._drag_y == 0:
            return
        x = self.root.winfo_pointerx() - self._drag_x
        y = self.root.winfo_pointery() - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def handle_click(self, event):
        if event.y <= 34 and WIDTH - 58 <= event.x <= WIDTH - 34:
            self.hide_to_tray()
        elif event.y <= 34 and event.x >= WIDTH - 32:
            self.quit_app()

    def hide_to_tray(self):
        self.root.withdraw()

    def show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", True))

    def quit_app(self):
        self.closing = True
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    def draw_loading(self):
        self.canvas.delete("all")
        self.round_rect(8, 8, WIDTH-8, HEIGHT-8, 18, fill="#101722", outline="#31455f")
        self.canvas.create_text(22, 24, text="London Weather", anchor="w", fill="#e8f4ff", font=("Segoe UI", 14, "bold"))
        self.canvas.create_text(WIDTH/2, HEIGHT/2, text="Loading weather data...", fill="#a9c3dc", font=("Segoe UI", 12))
        self.draw_window_controls()

    def draw_window_controls(self):
        self.canvas.create_text(WIDTH-48, 17, text="–", anchor="center", fill="#a9c3dc", font=("Segoe UI", 16, "bold"))
        self.canvas.create_text(WIDTH-18, 17, text="×", anchor="center", fill="#a9c3dc", font=("Segoe UI", 14, "bold"))

    def periodic_refresh(self):
        if not self.closing:
            self.fetch_async()
            self.root.after(REFRESH_MS, self.periodic_refresh)

    def fetch_async(self):
        def worker():
            try:
                with urllib.request.urlopen(API_URL, timeout=20) as r:
                    data = json.load(r)
                err = None
            except Exception as exc:
                data = None
                err = str(exc)
            if not self.closing:
                self.commands.put(("weather", data, err))
        threading.Thread(target=worker, daemon=True).start()

    def apply_weather_result(self, data, err):
        if data is not None:
            self.data = data
            self.error = None
        else:
            self.error = err
        self.draw()

    def current_london_hour(self):
        return datetime.now().hour

    def current_hour_index(self, hourly_times):
        now = datetime.now()
        current_slot = now.strftime("%Y-%m-%dT%H:00")
        if current_slot in hourly_times:
            return hourly_times.index(current_slot)
        return max(0, min(23, now.hour))

    def draw(self):
        self.canvas.delete("all")
        self.round_rect(8, 8, WIDTH-8, HEIGHT-8, 18, fill="#101722", outline="#31455f")
        self.canvas.create_text(22, 22, text="London Weather", anchor="w", fill="#e8f4ff", font=("Segoe UI", 14, "bold"))
        self.draw_window_controls()

        if self.error and not self.data:
            self.canvas.create_text(WIDTH/2, HEIGHT/2, text="Load failed", fill="#ffb4b4", font=("Segoe UI", 12, "bold"))
            self.canvas.create_text(WIDTH/2, HEIGHT/2+22, text=self.error[:52], fill="#ffb4b4", font=("Segoe UI", 8))
            return
        if not self.data:
            self.draw_loading()
            return

        hourly = self.data["hourly"]
        temps = hourly.get("temperature_2m", [None] * 24)
        rains = hourly["rain"]
        codes = hourly.get("weather_code", [None] * len(rains))
        winds = hourly.get("wind_speed_10m", [None] * len(rains))
        times = hourly.get("time", [])
        hour = self.current_hour_index(times)
        max_rain = max(max(rains), 1.0)
        temp_values = [t for t in temps if t is not None]
        min_temp = min(temp_values) if temp_values else 0
        max_temp = max(temp_values) if temp_values else 1
        temp_range = max(max_temp - min_temp, 1)
        wind_values = [w for w in winds if w is not None]
        min_wind = min(wind_values) if wind_values else 0
        max_wind = max(wind_values) if wind_values else 1
        wind_range = max(max_wind - min_wind, 1)

        gx0, gy0 = 48, 82
        gx1, gy1 = WIDTH - 56, HEIGHT - 78
        gw, gh = gx1 - gx0, gy1 - gy0
        self.chart_state = {
            "gx0": gx0, "gy0": gy0, "gx1": gx1, "gy1": gy1,
            "gw": gw, "rains": rains, "temps": temps, "winds": winds,
        }

        for frac, label in [(0, "0"), (0.5, f"{max_rain/2:.1f}"), (1, f"{max_rain:.1f}mm")]:
            y = gy1 - frac * gh
            self.canvas.create_line(gx0, y, gx1, y, fill="#203044")
            self.canvas.create_text(gx0-8, y, text=label, anchor="e", fill="#7891aa", font=("Segoe UI", 7))
        for tick_hour in [0, 6, 12, 18, 23]:
            x = gx0 + tick_hour / 23 * gw
            self.canvas.create_line(x, gy0, x, gy1, fill="#1a2839")
            self.canvas.create_text(x, gy1+13, text=f"{tick_hour:02d}", fill="#7891aa", font=("Segoe UI", 7))

        for i, rain in enumerate(rains):
            x = gx0 + i / 23 * gw
            bar_w = max(4, gw / 31)
            y = gy1 - (rain / max_rain) * gh if max_rain else gy1
            color = "#44c7ff" if rain > 0 else "#24364b"
            self.canvas.create_rectangle(x - bar_w/2, y, x + bar_w/2, gy1, fill=color, outline="")

        temp_points = []
        for i, temp in enumerate(temps):
            if temp is None:
                continue
            x = gx0 + i / 23 * gw
            y = gy1 - ((temp - min_temp) / temp_range) * gh
            temp_points.extend([x, y])
        if len(temp_points) >= 4:
            self.canvas.create_line(*temp_points, fill="#ff9f43", width=2, smooth=True)
        for i in range(0, len(temps), 6):
            temp = temps[i]
            if temp is None:
                continue
            x = gx0 + i / 23 * gw
            y = gy1 - ((temp - min_temp) / temp_range) * gh
            self.canvas.create_oval(x-2.2, y-2.2, x+2.2, y+2.2, fill="#ff9f43", outline="")
        if temp_values:
            temp_label_y = max(gy0 + 10, min(gy1 - 10, gy1 - ((temps[-1] - min_temp) / temp_range) * gh))
            self.canvas.create_text(gx1 + 8, temp_label_y, text="Temp", anchor="w", fill="#ff9f43", font=("Segoe UI", 9, "bold"))

        wind_points = []
        for i, wind in enumerate(winds[:24]):
            if wind is None:
                continue
            x = gx0 + i / 23 * gw
            y = gy1 - ((wind - min_wind) / wind_range) * gh
            wind_points.extend([x, y])
        if len(wind_points) >= 4:
            self.canvas.create_line(*wind_points, fill="#b58cff", width=2, dash=(6, 4), smooth=True)
        for i in range(0, min(len(winds), 24), 6):
            wind = winds[i]
            if wind is None:
                continue
            x = gx0 + i / 23 * gw
            y = gy1 - ((wind - min_wind) / wind_range) * gh
            self.canvas.create_oval(x-2.1, y-2.1, x+2.1, y+2.1, fill="#b58cff", outline="")
        if wind_values:
            wind_label_y = max(gy0 + 26, min(gy1 - 12, gy1 - ((winds[-1] - min_wind) / wind_range) * gh))
            self.canvas.create_text(gx1 + 8, wind_label_y, text="Wind", anchor="w", fill="#b58cff", font=("Segoe UI", 9, "bold"))
        self.canvas.create_text(gx1+8, gy0, text=f"{max_temp:.0f}°", anchor="w", fill="#ffb36b", font=("Segoe UI", 8))
        self.canvas.create_text(gx1+8, gy1, text=f"{min_temp:.0f}°", anchor="w", fill="#ffb36b", font=("Segoe UI", 8))
        self.canvas.create_text(gx0 + 10, gy1 - 12, text="Rain", anchor="w", fill="#44c7ff", font=("Segoe UI", 9, "bold"))

        mx = gx0 + hour / 23 * gw
        self.canvas.create_line(mx, gy0, mx, gy1, fill="#ffdf70", dash=(3, 4))
        self.canvas.create_text(mx, gy0-8, text="Now", fill="#ffdf70", font=("Segoe UI", 7))

        future = [(i, rains[i], codes[i]) for i in range(hour, len(rains))]
        rainy = [i for i, r, c in future if r > 0.05]
        end_text = "Little rain expected tonight"
        if rainy:
            last = max(rainy)
            end_text = "May continue late tonight" if last >= 23 else f"Main rain until around {last+1:02d}:00"
        peak_i = max(range(len(rains)), key=lambda i: rains[i])
        status_i = min(hour, len(codes)-1)
        status = WEATHER_LABELS.get(codes[status_i], "Weather")
        temp_text = f"｜{temps[status_i]:.0f}°C" if status_i < len(temps) and temps[status_i] is not None else ""
        wind_text = f"｜Wind {winds[status_i]:.0f} km/h" if status_i < len(winds) and winds[status_i] is not None else ""
        self.canvas.create_text(24, HEIGHT-30, text=f"{end_text} | Peak {peak_i:02d}:00 about {rains[peak_i]:.1f} mm | Now: {status}{temp_text}{wind_text}", anchor="w", fill="#e8f4ff", font=("Segoe UI", 10, "bold"))

    def clear_hover_value(self, event=None):
        self.canvas.delete("hover")

    def show_hover_value(self, event):
        state = self.chart_state
        if not state or not self.data:
            return
        gx0 = state["gx0"]
        gy0 = state["gy0"]
        gx1 = state["gx1"]
        gy1 = state["gy1"]
        gw = state["gw"]
        if event.x < gx0 or event.x > gx1 or event.y < gy0 - 18 or event.y > gy1 + 28:
            self.clear_hover_value()
            return

        rains = state["rains"]
        temps = state["temps"]
        winds = state.get("winds", [])
        count = min(len(rains), len(temps), len(winds) if winds else 24, 24)
        if count <= 0:
            return
        idx = max(0, min(count - 1, round((event.x - gx0) / gw * 23)))
        x = gx0 + idx / 23 * gw
        rain = rains[idx]
        temp = temps[idx]
        wind = winds[idx] if idx < len(winds) else None
        temp_part = f"{temp:.1f}°C" if temp is not None else "n/a"
        wind_part = f"{wind:.0f} km/h" if wind is not None else "n/a"
        text = f"{idx:02d}:00   Rain {rain:.1f} mm   Temp {temp_part}   Wind {wind_part}"

        self.canvas.delete("hover")
        self.canvas.create_line(x, gy0, x, gy1, fill="#d6e8ff", dash=(2, 3), tags="hover")
        self.canvas.create_oval(x-3, gy1-3, x+3, gy1+3, fill="#44c7ff", outline="", tags="hover")
        tx = min(max(x + 10, gx0 + 4), gx1 - 258)
        ty = gy0 + 8 if event.y > (gy0 + gy1) / 2 else gy1 - 34
        self.canvas.create_rectangle(tx, ty, tx + 256, ty + 24, fill="#121c29", outline="#31455f", tags="hover")
        self.canvas.create_text(tx + 8, ty + 12, text=text, anchor="w", fill="#e8f4ff", font=("Segoe UI", 8, "bold"), tags="hover")

    def round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
            x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
            x1, y2, x1, y2-r, x1, y1+r, x1, y1
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            if self.tray_icon:
                self.tray_icon.stop()


if __name__ == "__main__":
    _single_instance_mutex = ensure_single_instance()
    if _single_instance_mutex is None:
        sys.exit(0)
    FloatingRainApp().run()

