# -*- coding: utf-8 -*-
"""
VLC 视频播放器（优化中文版 v2.1）
功能：
- 独立暂停按钮，播放键负责播放/恢复
- 点击视频画面 = 暂停/播放（带前台窗口判断）
- 时间显示、键盘快捷键、全屏、静音、倍速
- 全屏时自动隐藏控制栏，鼠标移到屏幕底部才显示
- 进度条用 set_time 跳转，更精确可靠
- 播放结束自动重置状态
- 支持 [ / ] 快捷调整倍速
- 菜单栏：文件 → 打开 / 退出；帮助 → 关于（版本信息）
"""
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import sys

try:
    import vlc
except ImportError:
    vlc = None

# Windows：用于检测鼠标左键按下（VLC 接管视频区后 tkinter 收不到点击事件）
try:
    import ctypes
    _user32 = ctypes.windll.user32
except Exception:
    _user32 = None

VIDEO_FILETYPES = [
    ("MP4 视频", "*.mp4"),
    ("AVI 视频", "*.avi"),
    ("MOV 视频", "*.mov"),
    ("MKV 视频", "*.mkv"),
    ("FLV 视频", "*.flv"),
    ("WMV 视频", "*.wmv"),
    ("WebM 视频", "*.webm"),
    ("MP3 音频", "*.mp3"),
    ("WAV 音频", "*.wav"),
    ("FLAC 音频", "*.flac"),
    ("M4A 音频", "*.m4a"),
    ("所有文件", "*.*"),
]

# 全屏时鼠标靠近底部多少像素内显示控制栏
CONTROL_SHOW_MARGIN = 90
# 鼠标移开底部后多少毫秒自动隐藏控制栏
CONTROL_HIDE_DELAY = 700

# 视频区边距（初始化与恢复全屏共用）
VIDEO_PADX = 8
VIDEO_PADY_TOP = 8
VIDEO_PADY_BOTTOM = 4

APP_VERSION = "v1.0"


def fmt_time(ms: int) -> str:
    """毫秒 -> mm:ss 或 hh:mm:ss"""
    if ms < 0:
        ms = 0
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


class SimpleVLCPlayer:
    def __init__(self, root):
        self.root = root
        self.root.title("Simple Media Player")
        self.root.geometry("900x620")
        self.root.minsize(560, 420)

        if vlc is None:
            self.show_vlc_error()
            return

        try:
            self.instance = vlc.Instance()
            self.player = self.instance.media_player_new()
        except Exception as e:
            messagebox.showerror("错误", f"VLC 初始化失败：{e}")
            return

        # 状态
        self.current_path = None
        self.is_seeking = False
        self._fullscreen = False
        self._muted = False
        self._last_volume = 50
        self._lbtn_down = False
        self._hide_job = None
        self._progress_interval = 200  # 动态刷新间隔

        self.create_menu()
        self.create_widgets()
        self.bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.update_progress()
        if _user32 is not None:
            self._poll_click()

    # ---------- 菜单栏 ----------
    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="打开媒体文件...", command=self.load_video, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_closing, accelerator="Alt+F4")

        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self.show_about)

        # 绑定快捷键
        self.root.bind("<Control-o>", lambda e: self.load_video())
        self.root.bind("<Control-O>", lambda e: self.load_video())

    def show_about(self):
        """显示版本信息对话框"""
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        python_bits = "64-bit" if sys.maxsize > 2**32 else "32-bit"

        if vlc:
            try:
                vlc_lib_ver = vlc.libvlc_get_version().decode("utf-8") if isinstance(vlc.libvlc_get_version(), bytes) else vlc.libvlc_get_version()
            except Exception:
                vlc_lib_ver = "获取失败"
            try:
                vlc_py_ver = vlc.__version__ if hasattr(vlc, "__version__") else "未知"
            except Exception:
                vlc_py_ver = "未知"
        else:
            vlc_lib_ver = "未安装"
            vlc_py_ver = "未安装"

        info = (
            f"小航航专用媒体播放器  {APP_VERSION}\n"
            f"{'─' * 36}\n"
            f"Python 版本：{py_ver}  ({python_bits})\n"
            f"python-vlc：{vlc_py_ver}\n"
            f"VLC 库版本：{vlc_lib_ver}\n"
            f"{'─' * 36}\n"
            f"快捷键：\n"
            f"  F          全屏 / 退出全屏\n"
            f"  ← / →      快退 / 快进 5 秒\n"
            f"  ↑ / ↓      音量 +/-\n"
            f"  [ / ]      倍速 -0.25x / +0.25x\n"
            f"  M          静音切换\n"
            f"  Esc        退出全屏\n"
            f"  双击画面   全屏切换\n"
            f"  单击画面   暂停 / 播放"
        )
        messagebox.showinfo("关于", info)

    # ---------- 界面 ----------
    def show_vlc_error(self):
        frame = tk.Frame(self.root)
        frame.pack(expand=True)
        tk.Label(frame, text="未找到 VLC！", fg="red", font=("Arial", 14)).pack(pady=10)
        tk.Label(frame, text="请安装：").pack()
        tk.Label(frame, text="1. VLC 播放器（https://www.videolan.org/vlc/）").pack()
        tk.Label(frame, text="2. python-vlc：pip install python-vlc").pack(pady=5)

    def create_widgets(self):
        # 视频显示区域（双击全屏）
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(
            fill=tk.BOTH, expand=True,
            padx=VIDEO_PADX, pady=(VIDEO_PADY_TOP, VIDEO_PADY_BOTTOM)
        )
        self.video_frame.bind("<Double-Button-1>", lambda e: self.toggle_fullscreen())

        # 底部控制区（进度条 + 按钮 + 状态栏），全屏时可整体隐藏
        self.bottom_frame = tk.Frame(self.root)
        self.bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # 进度条 + 时间
        progress_frame = tk.Frame(self.bottom_frame)
        progress_frame.pack(fill=tk.X, padx=8, pady=2)

        self.progress = ttk.Scale(progress_frame, from_=0, to=100, orient=tk.HORIZONTAL)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress.bind("<ButtonPress-1>", self.progress_press)
        self.progress.bind("<ButtonRelease-1>", self.progress_release)

        self.time_label = tk.Label(progress_frame, text="00:00 / 00:00", width=15)
        self.time_label.pack(side=tk.RIGHT, padx=(8, 0))

        # 播放控制
        control_frame = tk.Frame(self.bottom_frame)
        control_frame.pack(fill=tk.X, padx=8, pady=4)

        self.load_btn = tk.Button(control_frame, text="打开", command=self.load_video, width=6)
        self.load_btn.pack(side=tk.LEFT, padx=2)

        self.play_btn = tk.Button(control_frame, text="▶ 播放", command=self.play_video, state=tk.DISABLED, width=8)
        self.play_btn.pack(side=tk.LEFT, padx=2)

        self.pause_btn = tk.Button(control_frame, text="⏸ 暂停", command=self.pause_video, state=tk.DISABLED, width=8)
        self.pause_btn.pack(side=tk.LEFT, padx=2)

        self.stop_btn = tk.Button(control_frame, text="■ 停止", command=self.stop_video, state=tk.DISABLED, width=6)
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        self.mute_btn = tk.Button(control_frame, text="🔊 静音", command=self.toggle_mute, width=8)
        self.mute_btn.pack(side=tk.LEFT, padx=2)

        self.full_btn = tk.Button(control_frame, text="⛶ 全屏", command=self.toggle_fullscreen, width=6)
        self.full_btn.pack(side=tk.LEFT, padx=2)

        # 倍速
        tk.Label(control_frame, text="倍速:").pack(side=tk.LEFT, padx=(12, 2))
        self.speed_var = tk.StringVar(value="1.0x")
        self.speed_box = ttk.Combobox(control_frame, textvariable=self.speed_var, width=5,
                                      values=["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"],
                                      state="readonly")
        self.speed_box.pack(side=tk.LEFT)
        self.speed_box.bind("<<ComboboxSelected>>", self.set_speed)

        # 音量
        tk.Label(control_frame, text="音量:").pack(side=tk.LEFT, padx=(12, 2))
        self.volume_scale = ttk.Scale(control_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                      command=self.set_volume, value=self._last_volume, length=100)
        self.volume_scale.pack(side=tk.LEFT, padx=2)

        # 状态栏
        self.status_label = tk.Label(self.bottom_frame,
                                     text="未加载媒体  |  点击画面=暂停/播放  ←→=快进快退  F=全屏  [ ]=倍速",
                                     anchor=tk.W, fg="gray")
        self.status_label.pack(fill=tk.X, padx=8, pady=(0, 4))

    def bind_keys(self):
        self.root.bind("<Left>", lambda e: self.seek_by(-5000))
        self.root.bind("<Right>", lambda e: self.seek_by(5000))
        self.root.bind("<Up>", lambda e: self.nudge_volume(5))
        self.root.bind("<Down>", lambda e: self.nudge_volume(-5))
        self.root.bind("f", lambda e: self.toggle_fullscreen())
        self.root.bind("F", lambda e: self.toggle_fullscreen())
        self.root.bind("m", lambda e: self.toggle_mute())
        self.root.bind("M", lambda e: self.toggle_mute())
        self.root.bind("<Escape>", lambda e: self.exit_fullscreen())
        self.root.bind("<bracketleft>", lambda e: self.nudge_speed(-0.25))
        self.root.bind("<bracketright>", lambda e: self.nudge_speed(0.25))

    # ---------- 播放控制 ----------
    def load_video(self):
        file_path = filedialog.askopenfilename(title="选择媒体文件", filetypes=VIDEO_FILETYPES)
        if not file_path:
            return
        try:
            self.current_path = file_path
            media = self.instance.media_new(file_path)
            self.player.set_media(media)
            self.player.set_hwnd(self.video_frame.winfo_id())
            self.play_btn.config(state=tk.NORMAL)
            self.pause_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.NORMAL)
            self.status_label.config(text=file_path)
            self.player.play()
            self.player.set_rate(float(self.speed_var.get().rstrip("x")))
            # 绑定播放结束事件
            self._attach_end_event()
        except Exception as e:
            messagebox.showerror("错误", f"加载失败：{e}")

    def _attach_end_event(self):
        """绑定播放结束事件，确保只绑定一次"""
        if hasattr(self, "_end_event_attached") and self._end_event_attached:
            return
        event_manager = self.player.event_manager()
        event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self.on_media_end)
        self._end_event_attached = True

    def on_media_end(self, event):
        """播放结束后在主线程中重置状态"""
        self.root.after(0, self.stop_video)

    def play_video(self):
        """播放按钮：负责播放和暂停后的恢复"""
        if not self.current_path:
            return
        self.player.play()
        self._update_status("播放中")

    def pause_video(self):
        """独立暂停按钮：只负责暂停，恢复播放请按 ▶ 播放"""
        if not self.current_path:
            return
        if self.player.get_state() == vlc.State.Playing:
            self.player.pause()
            self._update_status("已暂停")

    def stop_video(self):
        self.player.stop()
        self.progress.set(0)
        self.time_label.config(text="00:00 / 00:00")
        self._update_status("已停止")

    def seek_by(self, ms: int):
        if not self.current_path:
            return
        cur = self.player.get_time()
        self.player.set_time(max(0, cur + ms))

    # ---------- 进度 / 音量 ----------
    def progress_press(self, event):
        self.is_seeking = True

    def progress_release(self, event):
        if not self.current_path:
            self.is_seeking = False
            return
        pos = self.progress.get()
        length = self.player.get_length()
        if length > 0:
            target_ms = int(length * (pos / 100.0))
            self.player.set_time(target_ms)
        self.is_seeking = False

    def set_volume(self, value):
        vol = int(float(value))
        self._last_volume = max(vol, 1) if vol > 0 else self._last_volume
        self.player.audio_set_volume(vol)
        if self._muted and vol > 0:
            self._muted = False
            self.player.audio_set_mute(False)
            self.mute_btn.config(text="🔊 静音")

    def nudge_volume(self, delta: int):
        self.volume_scale.set(min(100, max(0, self.volume_scale.get() + delta)))

    def toggle_mute(self):
        self._muted = not self._muted
        self.player.audio_set_mute(self._muted)
        if self._muted:
            self.mute_btn.config(text="🔇 已静音")
        else:
            self.mute_btn.config(text="🔊 静音")
            self.player.audio_set_volume(int(self.volume_scale.get()))

    def set_speed(self, event=None):
        rate = float(self.speed_var.get().rstrip("x"))
        self.player.set_rate(rate)

    def nudge_speed(self, delta: float):
        """用 [ / ] 快捷键微调倍速"""
        current = float(self.speed_var.get().rstrip("x"))
        new_rate = round(current + delta, 2)
        # 限制在可选范围内
        valid_rates = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        closest = min(valid_rates, key=lambda x: abs(x - new_rate))
        self.speed_var.set(f"{closest}x")
        self.set_speed()

    # ---------- 全屏 ----------
    def toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)
        if self._fullscreen:
            # 进入全屏：去掉视频区四周白边，隐藏控制栏，开始轮询鼠标位置
            self.video_frame.pack_configure(padx=0, pady=0)
            self._hide_controls()
            self._poll_mouse()
        else:
            # 退出全屏：恢复边距、停止轮询、显示控制栏
            self.video_frame.pack_configure(
                padx=VIDEO_PADX, pady=(VIDEO_PADY_TOP, VIDEO_PADY_BOTTOM)
            )
            self._cancel_hide_job()
            self._show_controls()

    def exit_fullscreen(self):
        if self._fullscreen:
            self.toggle_fullscreen()

    def _poll_mouse(self):
        """全屏下定时检查鼠标位置：靠近底部显示控制栏，移开 0.7 秒后隐藏。
        注意：全屏时 VLC 接管了视频区的鼠标事件，tkinter 收不到 <Motion>，
        所以必须用 winfo_pointery() 轮询而不是事件绑定。
        正在拖动进度条（按着左键）时保持显示，避免拖到一半控制栏消失。"""
        if not self._fullscreen:
            return
        screen_h = self.root.winfo_screenheight()
        mouse_y = self.root.winfo_pointery()
        if mouse_y >= screen_h - CONTROL_SHOW_MARGIN:
            self._show_controls()
            self._cancel_hide_job()
        elif (self.bottom_frame.winfo_ismapped() and not self.is_seeking
              and not self._lbtn_down and self._hide_job is None):
            self._hide_job = self.root.after(CONTROL_HIDE_DELAY, self._auto_hide_controls)
        self.root.after(150, self._poll_mouse)

    def _show_controls(self):
        if not self.bottom_frame.winfo_ismapped():
            self.bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

    def _hide_controls(self):
        self.bottom_frame.pack_forget()

    def _auto_hide_controls(self):
        self._hide_job = None
        if self._fullscreen and not self.is_seeking and not self._lbtn_down:
            self._hide_controls()

    def _cancel_hide_job(self):
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None

    # ---------- 点击画面 暂停/播放 ----------
    def _poll_click(self):
        """轮询鼠标左键：VLC 接管视频区后 tkinter 收不到点击事件，
        用 Windows API 检测左键按下沿，再判断指针是否落在视频区内。
        处理函数包 try/except，防止异常中断轮询链。"""
        try:
            pressed = bool(_user32.GetAsyncKeyState(0x01) & 0x8000)  # VK_LBUTTON
        except Exception:
            pressed = False
        if pressed and not self._lbtn_down:
            try:
                self._on_video_click()
            except Exception:
                pass
        self._lbtn_down = pressed
        self.root.after(80, self._poll_click)

    def _on_video_click(self):
        """左键按下时若指针在视频区内：播放中则暂停，暂停中则播放。
        必须先确认播放器是前台活动窗口，否则点击其他窗口（坐标恰好
        与播放器窗口重叠）也会被误判。"""
        if not self.current_path:
            return
        try:
            foreground = _user32.GetForegroundWindow()
            own_window = self.root.winfo_id()
            if foreground != own_window:
                return
        except Exception:
            pass
        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        fx, fy = self.video_frame.winfo_rootx(), self.video_frame.winfo_rooty()
        fw, fh = self.video_frame.winfo_width(), self.video_frame.winfo_height()
        if not (fx <= x < fx + fw and fy <= y < fy + fh):
            return
        state = self.player.get_state()
        if state == vlc.State.Playing:
            self.player.pause()
            self._update_status("已暂停")
        elif state == vlc.State.Paused:
            self.player.play()
            self._update_status("播放中")

    # ---------- 状态栏 ----------
    def _update_status(self, state_text: str):
        """更新状态栏，保留原始提示信息"""
        base = "点击画面=暂停/播放  ←→=快进快退  F=全屏  [ ]=倍速"
        if self.current_path:
            self.status_label.config(text=f"{state_text}  |  {base}")
        else:
            self.status_label.config(text=f"未加载媒体  |  {base}")

    # ---------- 进度刷新 ----------
    def update_progress(self):
        if self.current_path and not self.is_seeking:
            try:
                length = self.player.get_length()
                current = self.player.get_time()
                if length > 0:
                    self.progress.set((current / length) * 100)
                    self.time_label.config(text=f"{fmt_time(current)} / {fmt_time(length)}")
                # 根据播放状态动态调整刷新频率
                state = self.player.get_state()
                self._progress_interval = 200 if state == vlc.State.Playing else 1000
            except Exception:
                pass
        self.root.after(self._progress_interval, self.update_progress)

    def on_closing(self):
        self.player.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = SimpleVLCPlayer(root)
    root.mainloop()
