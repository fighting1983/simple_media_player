# -*- coding: utf-8 -*-
"""
VLC 视频播放器(v1.2)
功能：
- 独立暂停按钮，播放键负责播放/恢复
- 点击视频画面 = 暂停/播放（带前台窗口判断）
- 时间显示、键盘快捷键（含空格暂停/播放）、全屏、静音、倍速
- 全屏时自动隐藏控制栏，鼠标移到屏幕底部才显示
- 进度条用 set_time 跳转，更精确可靠
- 播放结束自动重置状态 / 自动下一首（支持顺序/列表循环/单曲循环）
- 退出时记住播放进度、音量、倍速、循环模式、最近播放，下次启动自动续播
- 最近播放记录（最多 10 条，文件菜单下）
- 窗口置顶、画面比例切换(A 键)、AB 复读(B 键)
- 播完列表自动关机 / 30 分钟定时关机（工具菜单）
- 支持 [ / ] 快捷调整倍速
- 菜单栏：文件 → 打开单个 / 打开多个 / 打开文件夹 / 退出
- 播放列表：默认隐藏，双击播放，上一首/下一首，右键删除/清空
- 播放列表为空时按钮自动灰掉
- 音量数字实时显示
- 播放音频文件时视频区显示封面画面 + 装饰性柱状动画（可用 audio_cover.png 自定义图片）
"""
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import sys
import os
import json
import math
import random

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

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm",
              ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"}

# 音频格式：播放这些文件时视频区显示静态封面画面
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"}

# 音频封面上的装饰性柱状动画（纯装饰，不反映真实音频频谱）
COVER_BAR_COUNT = 32        # 柱子数量
COVER_ANIM_INTERVAL = 50    # 动画刷新间隔（毫秒）

VIDEO_FILETYPES = [
    ("所有文件", "*.*"),
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
]

# 全屏时鼠标靠近底部多少像素内显示控制栏
CONTROL_SHOW_MARGIN = 90
# 鼠标移开底部后多少毫秒自动隐藏控制栏
CONTROL_HIDE_DELAY = 700

# 视频区边距（初始化与恢复全屏共用）
VIDEO_PADX = 8
VIDEO_PADY_TOP = 8
VIDEO_PADY_BOTTOM = 4

APP_VERSION = "v1.2"

# 循环模式：顺序播放 / 列表循环 / 单曲循环
LOOP_MODES = ["顺序播放", "列表循环", "单曲循环"]

# 画面比例循环选项（A 键切换）
ASPECT_RATIOS = [("原始", None), ("16:9", "16:9"), ("4:3", "4:3"), ("1:1", "1:1")]

# 最近播放记录的最大条数
RECENT_MAX = 10

# 定时关机的分钟数
SHUTDOWN_TIMER_MINUTES = 30

# 配置保存路径（记住上次播放进度、音量、倍速、循环模式）
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_config.json")


def fmt_time(ms: int) -> str:
    """毫秒 -> mm:ss 或 hh:mm:ss"""
    if ms < 0:
        ms = 0
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def get_filename(path: str) -> str:
    """从完整路径提取文件名"""
    return os.path.basename(path) if path else "未知"


class SimpleVLCPlayer:
    def __init__(self, root):
        self.root = root
        self.root.title("简单媒体播放器")
        self.root.geometry("1100x680")
        self.root.minsize(760, 480)

        if vlc is None:
            self.show_vlc_error()
            return

        try:
            self.instance = vlc.Instance()
            self.player = self.instance.media_player_new()
        except Exception as e:
            messagebox.showerror("错误", f"VLC 初始化失败：{e}")
            return

        # 播放列表
        self.playlist = []          # 文件路径列表
        self.current_index = -1     # 当前播放索引

        # 状态
        self.is_seeking = False
        self._fullscreen = False
        self._muted = False
        self._last_volume = 50
        self._lbtn_down = False
        self._hide_job = None
        self._progress_interval = 200  # 动态刷新间隔
        self._playlist_visible = False  # 默认隐藏
        self._loop_mode = 0             # 循环模式索引（LOOP_MODES）
        self.recent_files = []          # 最近播放记录（最新在前）
        self._topmost_var = tk.BooleanVar(value=False)        # 窗口置顶
        self._shutdown_end_var = tk.BooleanVar(value=False)   # 播完列表后关机
        self._shutdown_timer_var = tk.BooleanVar(value=False) # 30 分钟后关机
        self._shutdown_job = None       # 定时关机的 after() 句柄
        self._aspect_idx = 0            # 画面比例索引（ASPECT_RATIOS）
        self._ab_a = None               # AB 复读 A 点（毫秒）
        self._ab_b = None               # AB 复读 B 点（毫秒）

        self.create_menu()
        self.create_widgets()
        self.bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.update_progress()
        if _user32 is not None:
            self._poll_click()
        self._update_playlist_buttons()
        self._load_config()

    # ---------- 菜单栏 ----------
    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="打开单个文件...", command=self.load_video, accelerator="Ctrl+O")
        file_menu.add_command(label="打开多个文件...", command=self.load_multiple_videos, accelerator="Ctrl+Shift+O")
        file_menu.add_command(label="打开文件夹...", command=self.load_folder, accelerator="Ctrl+F")
        # 最近播放子菜单
        self.recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="最近播放", menu=self.recent_menu)
        self._refresh_recent_menu()
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_closing, accelerator="Alt+F4")

        # 视图菜单
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="视图", menu=view_menu)
        view_menu.add_command(label="显示/隐藏播放列表", command=self.toggle_playlist, accelerator="Ctrl+L")
        view_menu.add_checkbutton(label="窗口置顶", variable=self._topmost_var,
                                  command=self._toggle_topmost)
        view_menu.add_command(label="切换画面比例", command=self.toggle_aspect, accelerator="A")

        # 工具菜单
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具", menu=tools_menu)
        tools_menu.add_checkbutton(label="播完列表后自动关机", variable=self._shutdown_end_var)
        tools_menu.add_checkbutton(label=f"{SHUTDOWN_TIMER_MINUTES} 分钟后自动关机",
                                   variable=self._shutdown_timer_var,
                                   command=self._toggle_shutdown_timer)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self.show_about)

        # 绑定快捷键
        self.root.bind("<Control-o>", lambda e: self.load_video())
        self.root.bind("<Control-O>", lambda e: self.load_video())
        self.root.bind("<Control-Shift-O>", lambda e: self.load_multiple_videos())
        self.root.bind("<Control-F>", lambda e: self.load_folder())
        self.root.bind("<Control-f>", lambda e: self.load_folder())
        self.root.bind("<Control-l>", lambda e: self.toggle_playlist())
        self.root.bind("<Control-L>", lambda e: self.toggle_playlist())

    def show_about(self):
        """显示版本信息对话框"""
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        python_bits = "64-bit" if sys.maxsize > 2**32 else "32-bit"

        if vlc:
            try:
                vlc_lib_ver = vlc.libvlc_get_version()
                if isinstance(vlc_lib_ver, bytes):
                    vlc_lib_ver = vlc_lib_ver.decode("utf-8")
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
            f"简单媒体播放器  {APP_VERSION}\n"
            f"{'─' * 36}\n"
            f"Python 版本：{py_ver}  ({python_bits})\n"
            f"python-vlc：{vlc_py_ver}\n"
            f"VLC 库版本：{vlc_lib_ver}\n"
            f"{'─' * 36}\n"
            f"快捷键：\n"
            f"  空格       暂停 / 播放\n"
            f"  F          全屏 / 退出全屏\n"
            f"  ← / →      快退 / 快进 5 秒\n"
            f"  ↑ / ↓      音量 +/-\n"
            f"  [ / ]      倍速 -0.25x / +0.25x\n"
            f"  M          静音切换\n"
            f"  A          切换画面比例\n"
            f"  B          AB 复读（设A点/设B点/取消）\n"
            f"  PgUp/PgDn  上一首 / 下一首\n"
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
        # ===== 左侧：播放列表面板（默认隐藏，不 pack）=====
        self.playlist_frame = tk.Frame(self.root, width=260)
        self.playlist_frame.pack_propagate(False)

        # 列表标题
        list_header = tk.Frame(self.playlist_frame, bg="#2b2b2b")
        list_header.pack(fill=tk.X)
        tk.Label(list_header, text="📋 播放列表", bg="#2b2b2b", fg="white",
                 font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT, padx=8, pady=6)
        self.count_label = tk.Label(list_header, text="(共 0 首)", bg="#2b2b2b", fg="#aaaaaa",
                 font=("Microsoft YaHei", 9))
        self.count_label.pack(side=tk.RIGHT, padx=8, pady=6)

        # 列表框 + 滚动条
        list_container = tk.Frame(self.playlist_frame)
        list_container.pack(fill=tk.BOTH, expand=True, padx=(4, 0), pady=(0, 4))

        scrollbar = tk.Scrollbar(list_container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.playlist_box = tk.Listbox(
            list_container, selectmode=tk.SINGLE, activestyle="none",
            font=("Microsoft YaHei", 10), bg="#1e1e1e", fg="#e0e0e0",
            selectbackground="#3a7bd5", selectforeground="white",
            highlightthickness=0, borderwidth=0,
            yscrollcommand=scrollbar.set
        )
        self.playlist_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.playlist_box.yview)

        self.playlist_box.bind("<Double-Button-1>", self.on_playlist_double_click)
        self.playlist_box.bind("<Button-3>", self.show_playlist_context_menu)
        self.playlist_box.bind("<<ListboxSelect>>", lambda e: self._update_playlist_buttons())

        # 播放列表控制按钮
        pl_btn_frame = tk.Frame(self.playlist_frame)
        pl_btn_frame.pack(fill=tk.X, padx=(4, 0), pady=(0, 6))

        self.pl_prev_btn = tk.Button(pl_btn_frame, text="⏮ 上一首", command=self.play_prev, width=10)
        self.pl_prev_btn.pack(side=tk.LEFT, padx=2)

        self.pl_next_btn = tk.Button(pl_btn_frame, text="⏭ 下一首", command=self.play_next, width=10)
        self.pl_next_btn.pack(side=tk.LEFT, padx=2)

        self.pl_clear_btn = tk.Button(pl_btn_frame, text="🗑 清空", command=self.clear_playlist, width=8)
        self.pl_clear_btn.pack(side=tk.RIGHT, padx=2)

        # ===== 右侧：视频 + 控制 =====
        self.right_frame = tk.Frame(self.root)
        self.right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 视频显示区域（双击全屏）
        self.video_frame = tk.Frame(self.right_frame, bg="black")
        self.video_frame.pack(
            fill=tk.BOTH, expand=True,
            padx=VIDEO_PADX, pady=(VIDEO_PADY_TOP, VIDEO_PADY_BOTTOM)
        )
        self.video_frame.bind("<Double-Button-1>", lambda e: self.toggle_fullscreen())

        # 音频封面：播放音频文件时盖住视频区的静态画面（默认不显示）
        self.audio_cover = tk.Frame(self.video_frame, bg="#1b1b2f")
        cover_inner = tk.Frame(self.audio_cover, bg="#1b1b2f")
        cover_inner.pack(expand=True)
        # 若脚本目录下存在 audio_cover.png 则优先显示该图片，否则显示音符图标
        self._cover_photo = None
        cover_png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_cover.png")
        if os.path.exists(cover_png):
            try:
                self._cover_photo = tk.PhotoImage(file=cover_png)
            except Exception:
                self._cover_photo = None
        if self._cover_photo is not None:
            tk.Label(cover_inner, image=self._cover_photo, bg="#1b1b2f").pack()
        else:
            tk.Label(cover_inner, text="🎵", bg="#1b1b2f", fg="#e0e0e0",
                     font=("Segoe UI Emoji", 72)).pack()
        self.cover_name_label = tk.Label(cover_inner, text="", bg="#1b1b2f", fg="#cccccc",
                                         font=("Microsoft YaHei", 12), wraplength=600)
        self.cover_name_label.pack(pady=(16, 0))

        # 装饰性柱状动画画布（纯装饰，不反映真实音频频谱）
        self.cover_canvas = tk.Canvas(self.audio_cover, height=120, bg="#1b1b2f",
                                      highlightthickness=0, bd=0)
        self.cover_canvas.pack(fill=tk.X, side=tk.BOTTOM, padx=40, pady=(0, 30))
        # 柱子颜色：从 #3a7bd5 到 #4fc3f7 的渐变
        c1, c2 = (58, 123, 213), (79, 195, 247)
        self._cover_bar_colors = [
            "#{:02x}{:02x}{:02x}".format(
                *[int(a + (b - a) * i / (COVER_BAR_COUNT - 1)) for a, b in zip(c1, c2)]
            ) for i in range(COVER_BAR_COUNT)
        ]
        self._cover_bars = [0.08] * COVER_BAR_COUNT    # 当前高度比例（0~1）
        self._cover_bar_targets = [0.08] * COVER_BAR_COUNT
        self._cover_phase = 0                          # 动画相位
        self._cover_anim_job = None                    # after() 任务句柄

        # 底部控制区
        self.bottom_frame = tk.Frame(self.right_frame)
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

        self.prev_btn = tk.Button(control_frame, text="⏮", command=self.play_prev, width=4)
        self.prev_btn.pack(side=tk.LEFT, padx=(8, 2))

        self.next_btn = tk.Button(control_frame, text="⏭", command=self.play_next, width=4)
        self.next_btn.pack(side=tk.LEFT, padx=2)

        self.mute_btn = tk.Button(control_frame, text="🔊 静音", command=self.toggle_mute, width=8)
        self.mute_btn.pack(side=tk.LEFT, padx=(8, 2))

        self.full_btn = tk.Button(control_frame, text="⛶ 全屏", command=self.toggle_fullscreen, width=6)
        self.full_btn.pack(side=tk.LEFT, padx=2)

        # 循环模式切换
        self.loop_btn = tk.Button(control_frame, text=f"🔁 {LOOP_MODES[self._loop_mode]}",
                                  command=self.toggle_loop_mode, width=9)
        self.loop_btn.pack(side=tk.LEFT, padx=(8, 2))

        # 倍速
        tk.Label(control_frame, text="倍速:").pack(side=tk.LEFT, padx=(12, 2))
        self.speed_var = tk.StringVar(value="1.0x")
        self.speed_box = ttk.Combobox(control_frame, textvariable=self.speed_var, width=5,
                                      values=["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"],
                                      state="readonly")
        self.speed_box.pack(side=tk.LEFT)
        self.speed_box.bind("<<ComboboxSelected>>", self.set_speed)

        # 音量 + 数字显示
        tk.Label(control_frame, text="音量:").pack(side=tk.LEFT, padx=(12, 2))
        self.volume_scale = ttk.Scale(control_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                      command=self.set_volume, value=self._last_volume, length=100)
        self.volume_scale.pack(side=tk.LEFT, padx=2)
        self.volume_label = tk.Label(control_frame, text="50", width=3, font=("Microsoft YaHei", 9, "bold"))
        self.volume_label.pack(side=tk.LEFT, padx=(0, 4))

        # 所有按钮不抢占键盘焦点，保证空格等快捷键点过按钮后依然有效
        for btn in (self.load_btn, self.play_btn, self.pause_btn, self.stop_btn,
                    self.prev_btn, self.next_btn, self.mute_btn, self.full_btn,
                    self.loop_btn, self.pl_prev_btn, self.pl_next_btn, self.pl_clear_btn):
            btn.config(takefocus=0)

        # 状态栏
        self.status_label = tk.Label(self.bottom_frame,
                                     text="未加载媒体  |  空格/点击画面=暂停播放  ←→=快进快退  F=全屏  [ ]=倍速  PgUp/PgDn=切歌",
                                     anchor=tk.W, fg="gray")
        self.status_label.pack(fill=tk.X, padx=8, pady=(0, 4))

    def bind_keys(self):
        self.root.bind_all("<space>", self._on_space)
        self.root.bind("<Left>", lambda e: self.seek_by(-5000))
        self.root.bind("<Right>", lambda e: self.seek_by(5000))
        self.root.bind("<Up>", lambda e: self.nudge_volume(5))
        self.root.bind("<Down>", lambda e: self.nudge_volume(-5))
        self.root.bind("f", lambda e: self.toggle_fullscreen())
        self.root.bind("F", lambda e: self.toggle_fullscreen())
        self.root.bind("m", lambda e: self.toggle_mute())
        self.root.bind("M", lambda e: self.toggle_mute())
        self.root.bind("a", lambda e: self.toggle_aspect())
        self.root.bind("A", lambda e: self.toggle_aspect())
        self.root.bind("b", lambda e: self.toggle_ab())
        self.root.bind("B", lambda e: self.toggle_ab())
        self.root.bind("<Escape>", lambda e: self.exit_fullscreen())
        self.root.bind("<bracketleft>", lambda e: self.nudge_speed(-0.25))
        self.root.bind("<bracketright>", lambda e: self.nudge_speed(0.25))
        self.root.bind("<Prior>", lambda e: self.play_prev())   # PgUp
        self.root.bind("<Next>", lambda e: self.play_next())    # PgDn

    # ---------- 播放列表操作 ----------
    def _update_playlist_buttons(self):
        """根据播放列表状态更新按钮可用性"""
        has_items = len(self.playlist) > 0
        has_selection = len(self.playlist_box.curselection()) > 0
        state_items = tk.NORMAL if has_items else tk.DISABLED

        self.pl_prev_btn.config(state=state_items)
        self.pl_next_btn.config(state=state_items)
        self.pl_clear_btn.config(state=state_items)
        self.prev_btn.config(state=state_items)
        self.next_btn.config(state=state_items)
        self.play_btn.config(state=state_items)

    def toggle_playlist(self):
        """显示/隐藏播放列表"""
        if self._playlist_visible:
            self.playlist_frame.pack_forget()
            self._playlist_visible = False
        else:
            self.playlist_frame.pack(side=tk.LEFT, fill=tk.Y, before=self.right_frame)
            self._playlist_visible = True

    def add_to_playlist(self, paths):
        """向播放列表添加文件路径（去重）"""
        added = 0
        for p in paths:
            if p not in self.playlist:
                self.playlist.append(p)
                self.playlist_box.insert(tk.END, get_filename(p))
                added += 1
        self._update_count()
        self._update_playlist_buttons()
        if added > 0 and self.current_index == -1:
            self.play_at(0)

    def _update_audio_cover(self, path: str = None):
        """播放音频文件时显示封面画面（含装饰性柱状动画），其余情况隐藏"""
        if path and os.path.splitext(path)[1].lower() in AUDIO_EXTS:
            self.cover_name_label.config(text=get_filename(path))
            self.audio_cover.place(relx=0, rely=0, relwidth=1, relheight=1)
            if self._cover_anim_job is None:
                self._cover_anim_job = self.root.after(0, self._animate_cover)
        else:
            self.audio_cover.place_forget()
            if self._cover_anim_job is not None:
                self.root.after_cancel(self._cover_anim_job)
                self._cover_anim_job = None

    def _animate_cover(self):
        """柱状动画 tick：播放中让目标高度按叠加正弦 + 随机扰动跳动，
        暂停时回落到低矮静止柱；当前高度始终向目标平滑过渡。"""
        playing = False
        try:
            playing = self.player.get_state() == vlc.State.Playing
        except Exception:
            pass
        if playing:
            self._cover_phase += 1
            t = self._cover_phase
            for i in range(COVER_BAR_COUNT):
                v = (0.45 + 0.30 * math.sin(t * 0.35 + i * 0.6)
                          + 0.25 * math.sin(t * 0.13 + i * 1.7))
                self._cover_bar_targets[i] = max(0.05, min(1.0, v + random.uniform(-0.08, 0.08)))
        else:
            self._cover_bar_targets = [0.08] * COVER_BAR_COUNT
        for i in range(COVER_BAR_COUNT):
            cur = self._cover_bars[i]
            self._cover_bars[i] = cur + (self._cover_bar_targets[i] - cur) * 0.3
        self._draw_cover_bars()
        self._cover_anim_job = self.root.after(COVER_ANIM_INTERVAL, self._animate_cover)

    def _draw_cover_bars(self):
        """按当前高度比例重绘柱子"""
        c = self.cover_canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10:
            return
        c.delete("bar")
        gap = 3
        bw = (w - gap * (COVER_BAR_COUNT - 1)) / COVER_BAR_COUNT
        for i, ratio in enumerate(self._cover_bars):
            bh = max(2, ratio * (h - 4))
            x0 = i * (bw + gap)
            c.create_rectangle(x0, h - bh, x0 + bw, h,
                               fill=self._cover_bar_colors[i], width=0, tags="bar")

    def play_at(self, index: int):
        """播放指定索引的文件"""
        if not (0 <= index < len(self.playlist)):
            return
        self.current_index = index
        path = self.playlist[index]

        # 高亮当前项
        self.playlist_box.selection_clear(0, tk.END)
        self.playlist_box.selection_set(index)
        self.playlist_box.see(index)

        try:
            media = self.instance.media_new(path)
            self.player.set_media(media)
            self.player.set_hwnd(self.video_frame.winfo_id())
            self.pause_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.NORMAL)
            self.status_label.config(text=f"[{index + 1}/{len(self.playlist)}] {get_filename(path)}")
            self.player.play()
            self.player.set_rate(float(self.speed_var.get().rstrip("x")))
            self._update_audio_cover(path)
            self._add_recent(path)
            # 切换文件时重置 AB 复读点
            self._ab_a = self._ab_b = None
            # 绑定播放结束事件
            self._attach_end_event()
        except Exception as e:
            messagebox.showerror("错误", f"加载失败：{e}")

    def play_next(self):
        """下一首"""
        if not self.playlist:
            return
        next_idx = self.current_index + 1
        if next_idx >= len(self.playlist):
            next_idx = 0
        self.play_at(next_idx)

    def play_prev(self):
        """上一首"""
        if not self.playlist:
            return
        prev_idx = self.current_index - 1
        if prev_idx < 0:
            prev_idx = len(self.playlist) - 1
        self.play_at(prev_idx)

    def on_playlist_double_click(self, event):
        """双击播放列表项"""
        selection = self.playlist_box.curselection()
        if selection:
            self.play_at(selection[0])

    def show_playlist_context_menu(self, event):
        """右键菜单"""
        menu = tk.Menu(self.root, tearoff=0)
        has_selection = len(self.playlist_box.curselection()) > 0
        has_items = len(self.playlist) > 0

        menu.add_command(label="播放", command=lambda: self.on_playlist_double_click(None),
                         state=tk.NORMAL if has_selection else tk.DISABLED)
        menu.add_command(label="删除", command=self.delete_selected_item,
                         state=tk.NORMAL if has_selection else tk.DISABLED)
        menu.add_separator()
        menu.add_command(label="清空列表", command=self.clear_playlist,
                         state=tk.NORMAL if has_items else tk.DISABLED)
        menu.post(event.x_root, event.y_root)

    def delete_selected_item(self):
        """删除选中的列表项"""
        selection = self.playlist_box.curselection()
        if not selection:
            return
        idx = selection[0]
        self.playlist_box.delete(idx)
        del self.playlist[idx]
        if idx == self.current_index:
            self.current_index = -1
            self.stop_video()
        elif idx < self.current_index:
            self.current_index -= 1
        self._update_count()
        self._update_playlist_buttons()

    def clear_playlist(self):
        """清空播放列表"""
        self.playlist.clear()
        self.playlist_box.delete(0, tk.END)
        self.current_index = -1
        self.stop_video()
        self._update_count()
        self._update_playlist_buttons()

    def _update_count(self):
        """更新列表数量显示"""
        self.count_label.config(text=f"(共 {len(self.playlist)} 首)")

    # ---------- 最近播放 ----------
    def _add_recent(self, path: str):
        """把文件记入最近播放（去重、最新在前、最多 RECENT_MAX 条）"""
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        del self.recent_files[RECENT_MAX:]
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        """重建最近播放子菜单"""
        self.recent_menu.delete(0, tk.END)
        if not self.recent_files:
            self.recent_menu.add_command(label="（空）", state=tk.DISABLED)
            return
        for p in self.recent_files:
            self.recent_menu.add_command(label=get_filename(p),
                                         command=lambda x=p: self._open_recent(x))
        self.recent_menu.add_separator()
        self.recent_menu.add_command(label="清空记录", command=self._clear_recent)

    def _open_recent(self, path: str):
        """打开最近播放记录中的文件"""
        if not os.path.exists(path):
            messagebox.showinfo("提示", f"文件已不存在：\n{path}")
            self.recent_files.remove(path)
            self._refresh_recent_menu()
            return
        if path in self.playlist:
            self.play_at(self.playlist.index(path))
        else:
            self.add_to_playlist([path])
            self.play_at(len(self.playlist) - 1)

    def _clear_recent(self):
        self.recent_files.clear()
        self._refresh_recent_menu()

    # ---------- 视图：置顶 / 画面比例 ----------
    def _toggle_topmost(self):
        self.root.attributes("-topmost", self._topmost_var.get())

    def toggle_aspect(self):
        """循环切换画面比例（A 键）"""
        self._aspect_idx = (self._aspect_idx + 1) % len(ASPECT_RATIOS)
        name, value = ASPECT_RATIOS[self._aspect_idx]
        try:
            self.player.video_set_aspect_ratio(value)
        except Exception:
            pass
        self._update_status(f"画面比例：{name}")

    # ---------- AB 复读 ----------
    def toggle_ab(self):
        """B 键三次循环：设 A 点 → 设 B 点（开始复读）→ 取消"""
        if self.current_index == -1:
            return
        if self._ab_a is None:
            self._ab_a = self.player.get_time()
            self._update_status("复读：已设 A 点，再按 B 设终点")
        elif self._ab_b is None:
            t = self.player.get_time()
            if t <= self._ab_a:
                return  # B 点必须晚于 A 点
            self._ab_b = t
            self._update_status(f"复读中：{fmt_time(self._ab_a)} → {fmt_time(self._ab_b)}（再按 B 取消）")
        else:
            self._ab_a = self._ab_b = None
            self._update_status("复读：已取消")

    # ---------- 定时关机 ----------
    def _toggle_shutdown_timer(self):
        """30 分钟定时关机开关"""
        if self._shutdown_timer_var.get():
            self._shutdown_job = self.root.after(
                SHUTDOWN_TIMER_MINUTES * 60 * 1000, self._do_shutdown)
            self._update_status(f"已设置 {SHUTDOWN_TIMER_MINUTES} 分钟后自动关机")
        else:
            if self._shutdown_job is not None:
                self.root.after_cancel(self._shutdown_job)
                self._shutdown_job = None
            self._update_status("已取消定时关机")

    def _do_shutdown(self):
        """执行关机：60 秒倒计时，期间运行 shutdown /a 可取消"""
        self._shutdown_job = None
        try:
            self.player.stop()
        except Exception:
            pass
        os.system('shutdown /s /t 60')

    # ---------- 文件加载 ----------
    def load_video(self):
        """打开单个文件并立即播放"""
        file_path = filedialog.askopenfilename(title="选择媒体文件", filetypes=VIDEO_FILETYPES)
        if not file_path:
            return
        if file_path in self.playlist:
            # 已在列表中则直接播放已有项
            self.play_at(self.playlist.index(file_path))
        else:
            self.add_to_playlist([file_path])
            self.play_at(len(self.playlist) - 1)

    def load_multiple_videos(self):
        """打开多个文件"""
        files = filedialog.askopenfilenames(title="选择多个媒体文件", filetypes=VIDEO_FILETYPES)
        if files:
            self.add_to_playlist(list(files))

    def load_folder(self):
        """打开文件夹，递归扫描媒体文件"""
        folder = filedialog.askdirectory(title="选择包含媒体文件的文件夹")
        if not folder:
            return
        files = []
        for root_dir, _, filenames in os.walk(folder):
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext in VIDEO_EXTS:
                    files.append(os.path.join(root_dir, name))
        if files:
            files.sort()
            self.add_to_playlist(files)
            messagebox.showinfo("导入完成", f"共导入 {len(files)} 个媒体文件")
        else:
            messagebox.showinfo("提示", "该文件夹下未找到支持的媒体文件")

    def _attach_end_event(self):
        """绑定播放结束事件，确保只绑定一次"""
        if hasattr(self, "_end_event_attached") and self._end_event_attached:
            return
        event_manager = self.player.event_manager()
        event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self.on_media_end)
        self._end_event_attached = True

    def on_media_end(self, event):
        """播放结束后在主线程中重置状态"""
        self.root.after(0, self._auto_next)

    def _auto_next(self):
        """播放结束后的行为，按循环模式处理"""
        if not (self.playlist and self.current_index >= 0):
            return
        if self._loop_mode == 2:  # 单曲循环
            self.play_at(self.current_index)
            return
        next_idx = self.current_index + 1
        if next_idx < len(self.playlist):
            self.play_at(next_idx)
        elif self._loop_mode == 1:  # 列表循环：回到第一首
            self.play_at(0)
        else:  # 顺序播放：播完即止
            self.current_index = -1
            self.stop_video()
            self.playlist_box.selection_clear(0, tk.END)
            self.status_label.config(text="列表播放完毕")
            if self._shutdown_end_var.get():
                self._do_shutdown()

    def toggle_loop_mode(self):
        """循环切换：顺序播放 → 列表循环 → 单曲循环"""
        self._loop_mode = (self._loop_mode + 1) % len(LOOP_MODES)
        self.loop_btn.config(text=f"🔁 {LOOP_MODES[self._loop_mode]}")

    # ---------- 播放控制 ----------
    def _on_space(self, event):
        """空格键：播放中则暂停，否则播放/恢复。
        返回 "break" 阻止事件继续传播（按钮已设 takefocus=0，
        正常不会抢焦点，这里只是兜底）。"""
        if self.current_index >= 0:
            if self.player.get_state() == vlc.State.Playing:
                self.pause_video()
            else:
                self.play_video()
        return "break"
    def play_video(self):
        """播放按钮：负责播放和暂停后的恢复"""
        if not self.playlist:
            return
        if self.current_index == -1:
            self.play_at(0)
            return
        self.player.play()
        self._update_status("播放中")

    def pause_video(self):
        """独立暂停按钮：只负责暂停，恢复播放请按 ▶ 播放"""
        if self.current_index == -1:
            return
        if self.player.get_state() == vlc.State.Playing:
            self.player.pause()
            self._update_status("已暂停")

    def stop_video(self):
        self.player.stop()
        self.progress.set(0)
        self.time_label.config(text="00:00 / 00:00")
        self._update_audio_cover()
        self._update_status("已停止")

    def seek_by(self, ms: int):
        if self.current_index == -1:
            return
        cur = self.player.get_time()
        self.player.set_time(max(0, cur + ms))

    # ---------- 进度 / 音量 ----------
    def progress_press(self, event):
        self.is_seeking = True

    def progress_release(self, event):
        if self.current_index == -1:
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
        # 更新音量数字显示
        if hasattr(self, 'volume_label'):
            self.volume_label.config(text=f"{vol}")
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
            if hasattr(self, 'volume_label'):
                self.volume_label.config(text="0")
        else:
            self.mute_btn.config(text="🔊 静音")
            vol = int(self.volume_scale.get())
            self.player.audio_set_volume(vol)
            if hasattr(self, 'volume_label'):
                self.volume_label.config(text=f"{vol}")

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
        if self.current_index == -1:
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
        base = "空格/点击画面=暂停播放  ←→=快进快退  F=全屏  [ ]=倍速  PgUp/PgDn=切歌"
        if self.current_index >= 0:
            name = get_filename(self.playlist[self.current_index])
            self.status_label.config(text=f"[{self.current_index + 1}/{len(self.playlist)}] {state_text}  |  {base}")
        else:
            self.status_label.config(text=f"未加载媒体  |  {base}")

    # ---------- 进度刷新 ----------
    def update_progress(self):
        if self.current_index >= 0 and not self.is_seeking:
            try:
                length = self.player.get_length()
                current = self.player.get_time()
                if length > 0:
                    self.progress.set((current / length) * 100)
                    self.time_label.config(text=f"{fmt_time(current)} / {fmt_time(length)}")
                # AB 复读：越过 B 点跳回 A 点
                if self._ab_a is not None and self._ab_b is not None and current > self._ab_b:
                    self.player.set_time(self._ab_a)
                # 根据播放状态动态调整刷新频率
                state = self.player.get_state()
                self._progress_interval = 200 if state == vlc.State.Playing else 1000
            except Exception:
                pass
        self.root.after(self._progress_interval, self.update_progress)

    # ---------- 配置保存 / 恢复 ----------
    def _save_config(self):
        """退出时保存：当前文件、播放进度、音量、倍速、循环模式"""
        data = {
            "file": None,
            "position_ms": 0,
            "volume": int(self.volume_scale.get()),
            "speed": self.speed_var.get(),
            "loop_mode": self._loop_mode,
            "recent": self.recent_files,
            "topmost": self._topmost_var.get(),
        }
        if self.playlist and self.current_index >= 0:
            data["file"] = self.playlist[self.current_index]
            try:
                data["position_ms"] = max(0, self.player.get_time())
            except Exception:
                pass
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_config(self):
        """启动时恢复上次状态；上次播放的文件还在则自动续播"""
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return
        # 音量（set 会触发 set_volume 同步到播放器）
        try:
            self.volume_scale.set(max(0, min(100, int(cfg.get("volume", 50)))))
        except Exception:
            pass
        # 倍速
        speed = str(cfg.get("speed", "1.0x"))
        if speed in self.speed_box["values"]:
            self.speed_var.set(speed)
        # 循环模式
        loop_mode = cfg.get("loop_mode", 0)
        if isinstance(loop_mode, int) and 0 <= loop_mode < len(LOOP_MODES):
            self._loop_mode = loop_mode
            self.loop_btn.config(text=f"🔁 {LOOP_MODES[self._loop_mode]}")
        # 最近播放
        recent = cfg.get("recent", [])
        if isinstance(recent, list):
            self.recent_files = [p for p in recent if isinstance(p, str)][:RECENT_MAX]
            self._refresh_recent_menu()
        # 窗口置顶
        if cfg.get("topmost"):
            self._topmost_var.set(True)
            self._toggle_topmost()
        # 续播上次的文件
        path = cfg.get("file")
        if path and os.path.exists(path):
            self.add_to_playlist([path])  # 空闲状态会自动播放
            pos = cfg.get("position_ms", 0)
            if isinstance(pos, (int, float)) and pos > 0:
                self.root.after(500, lambda: self._resume_position(int(pos)))

    def _resume_position(self, ms: int, attempts: int = 20):
        """等媒体解析完成后跳转到指定进度（未就绪则每 0.5 秒重试）"""
        if self.current_index == -1:
            return
        try:
            if self.player.get_length() > 0:
                self.player.set_time(ms)
                return
        except Exception:
            return
        if attempts > 0:
            self.root.after(500, lambda: self._resume_position(ms, attempts - 1))

    def on_closing(self):
        try:
            if self._shutdown_job is not None:
                self.root.after_cancel(self._shutdown_job)
                self._shutdown_job = None
            self._save_config()
            self.player.stop()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = SimpleVLCPlayer(root)
    root.mainloop()
