from __future__ import annotations

import dataclasses
import logging
import time
from functools import partial
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker import __version__
from ptztracker.api.backend import start_window_api
from ptztracker.camera.preset_thumbs import PresetThumbs
from ptztracker.config import (
    GRID_SLOTS,
    AppConfig,
    CameraConfig,
    ConfigStore,
    get_password,
    set_password,
)
from ptztracker.control.zones import Zone
from ptztracker.gamepad.device import GamepadThread, MultiPadThread, SdlBackend, SdlHub
from ptztracker.multicam.grid import CameraGrid, Cell
from ptztracker.multicam.pads import PadFeedback, PadRouter
from ptztracker.multicam.timeline import StateTimeline
from ptztracker.multicam.velocity import MixerSource
from ptztracker.system_load import SystemLoad
from ptztracker.tally.atem import AtemTally
from ptztracker.ui.agent_panel import AgentPanel
from ptztracker.ui.camera_cell import CameraCell
from ptztracker.ui.camera_dialog import CameraDialog, merge_edit
from ptztracker.ui.camera_menu import CameraMenu
from ptztracker.ui.camera_strip import CameraStrip
from ptztracker.ui.follow_panel import SIZES, FollowPanel
from ptztracker.ui.gamepad_bridge import GamepadBridge
from ptztracker.ui.gamepads_dialog import GamepadsDialog
from ptztracker.ui.hints import HintBar
from ptztracker.ui.hotkeys_dialog import HotkeysDialog
from ptztracker.ui.keys import digit_of, logical_key
from ptztracker.ui.load_meter import LoadMeter
from ptztracker.ui.main_menu import MainMenu
from ptztracker.ui.manual_controls import ManualControls
from ptztracker.ui.presets_drawer import PresetsDrawer
from ptztracker.ui.preview import PreviewWidget
from ptztracker.ui.record_status import RecordPill
from ptztracker.ui.service_cell import KINDS, ServiceCell, WelcomeView
from ptztracker.ui.state import (
    TrackingView,
)
from ptztracker.ui.status_bar import MessageStatusBar
from ptztracker.ui.theme import FS_CAPTION, R_BUTTON, SP, C
from ptztracker.ui.widgets import (
    BrandMark,
    KeyButton,
    PadBadge,
    StateTag,
    StatusPill,
    VideoFrame,
)
from ptztracker.ui.window_cells import CellsMixin
from ptztracker.ui.window_common import (
    _HOTKEYS,
    _KB_MOUSE_EVENTS,
    _MENU_KEYS,
    _PAN_KEYS,
    _WASD,
    _ZOOM_IN_KEYS,
    _ZOOM_OUT_KEYS,
    CYCLE_KINDS,
    KEY_PAN_SPEED,
    SIDE_WIDTH,
    STRIP_MS,
    _vline,
)
from ptztracker.ui.window_follow import FollowMixin
from ptztracker.ui.window_input import InputMixin
from ptztracker.ui.window_pads import PadsMixin
from ptztracker.ui.window_presets import PresetsMixin
from ptztracker.ui.window_recording import RecordingMixin
from ptztracker.ui.window_status import StatusMixin
from ptztracker.ui.window_videohub import VideohubMixin
from ptztracker.updates import UpdateChecker, UpdateInfo
from ptztracker.video.rtsp import STREAM_CHOICES
from ptztracker.videohub.client import VIDEOHUB_PORT
from ptztracker.videorec.controller import VideoController

__all__ = ["MainWindow", "SIM_CAMERA", "sim_cameras", "make_pad_backend", "make_pad_hub", "_HOTKEYS", "KEY_PAN_SPEED", "SIDE_WIDTH", "STRIP_MS"]  # снаружи окно берут отсюда

log = logging.getLogger(__name__)


SIM_CAMERA = CameraConfig(id="sim", name="Симулятор", ip="sim", kind="sim")  # образец для sim1…sim4


def sim_cameras() -> list[CameraConfig]:
    """Четыре виртуальные камеры для --sim: sim1…sim4, «Симулятор 1…4»."""
    return [dataclasses.replace(SIM_CAMERA, id=f"sim{i}", name=f"Симулятор {i}") for i in range(1, GRID_SLOTS + 1)]


def make_pad_backend():
    """Устройство геймпада по умолчанию (тесты подменяют)."""
    return SdlBackend()


def make_pad_hub():
    """Общий SDL для нескольких геймпадов (тесты подменяют)."""
    return SdlHub()


class MainWindow(CellsMixin, FollowMixin, PresetsMixin, RecordingMixin, StatusMixin, InputMixin, PadsMixin,
                 VideohubMixin, QMainWindow):
    _saveFinished = Signal(object)  # (пачка, Future записи); приходит из рабочего потока, обрабатывается в Qt-потоке

    def __init__(self, store: ConfigStore, use_sim: bool = False, parent=None, atem_port: int = 9910,
                 checker_factory: Callable[[str], UpdateChecker] | None = UpdateChecker, gamepad_backend=None,
                 gamepad_hub=None, random_sim: bool = False, videohub_port: int = VIDEOHUB_PORT):
        super().__init__(parent)
        self.setWindowTitle("PTZ Tracker")
        self.resize(1640, 850)
        self.store = store
        self.cfg: AppConfig = store.load()
        self.use_sim = use_sim
        self.random_sim = random_sim  # запуск с --sim: случайный маршрут докладчика (в тестах — прежняя сцена)
        # проверка новой версии: в --sim сеть не трогаем; в тестах подставляют фальшивку
        self._checker_factory = None if use_sim and checker_factory is UpdateChecker else checker_factory
        self._checker: UpdateChecker | None = None
        self._update_info: UpdateInfo | None = None
        self._sim_cams = sim_cameras() if use_sim else []
        self.grid = CameraGrid(self._make_session)
        self.cells: list[CameraCell] = [CameraCell(i) for i in range(GRID_SLOTS)]
        for i, cell in enumerate(self.cells):
            cell.preview.rec_clicked.connect(partial(self.toggle_video, i))
        self.grid.selected = 0
        self._init_videohub(videohub_port)
        self._build_cells(atem_port)
        self._build_gamepads(gamepad_backend, gamepad_hub)
        stream_menu = self._build_top_bar()
        grid_host, side_col = self._build_side_panel()
        self._build_central(store, grid_host, side_col)
        self._build_status_bar()
        self._build_menus(stream_menu)
        self._start_services()

    def _build_cells(self, atem_port):
        """Клетки сетки и их сигналы, сетка камер, ATEM."""
        # окно владеет (Qt-родитель — окно, иначе их связи держали бы окно в памяти и после закрытия),
        # клетки только показывают
        self._service = {k: ServiceCell(k, self) for k in KINDS}
        self._service_slot: dict[str, int | None] = dict.fromkeys(KINDS)
        self.welcome = WelcomeView(self)  # ни одной камеры в сетке — в первой клетке
        for wd in (self.welcome, *self._service.values()):
            wd.hide()
        self.welcome.addRequested.connect(lambda: self._add_camera())
        self.welcome.simRequested.connect(lambda: self._open_sim_camera())
        self._welcome_on = False
        self._loading = True  # пока строится окно: анализ и настройки не пересчитываем на каждую клетку
        self._srcs: dict[tuple[int, str], tuple[Cell, MixerSource]] = {}  # источники смесителя клетки (кэш)
        self.pad_router = PadRouter()  # какой геймпад ведёт какую клетку
        self.on_pad_feedback: Callable[[PadFeedback], None] | None = None  # точки/вибрация для геймпада n (и тесты)
        self.pads: dict[int, GamepadBridge] = {}  # номер в маршрутизаторе → мост физического геймпада
        self._pad_nums: dict[GamepadBridge, int] = {}
        self._pad_vel: dict[int, tuple[float, float, float]] = {}  # что геймпад n держит физически
        self._menu_pad: int | None = None  # меню Options открыто с геймпада n — действует на его камеру
        self._menu_host: VideoFrame | None = None
        self._speed_scale_saved: dict[int, float] = {}
        self._tag_shown: dict[int, TrackingView | None] = {}
        self._tracking_set: frozenset[int] = frozenset()
        self._cell_dialogs: dict[int, QDialog] = {}
        self._held: set[int] = set()
        self._fast = False  # Shift зажат
        self.last_input = "kb"  # чем управляли последним: "kb" (клавиатура и мышь) | "pad" — по нему подсказка
        # режим «камера как курсор»: клетка, колесо (zoom | iris), плашка сверху по центру видео
        self.mouse_slot: int | None = None
        self.mouse_wheel = "zoom"
        self.mouse_hint = HintBar()
        self.mouse_hint.hide()
        self._wheel_timer = QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.timeout.connect(lambda: self._mouse_src() and self._mouse_src().set_zoom_velocity(0.0))
        self._atem_port = atem_port
        self.atem: AtemTally | None = None
        self._tally_shown: tuple | None = None

    def _build_gamepads(self, gamepad_backend, gamepad_hub):
        """Геймпады: мост, опрос, маршрутизатор."""
        # геймпад: свой поток опроса; без SDL/устройства мост ничего не делает. Mapper читает camera_menu.is_open.
        self._pad_multi: MultiPadThread | None = None
        self._bridges: list[GamepadBridge] = []  # по индексам хаба; self.pad — первый
        if gamepad_backend is None and gamepad_hub is None:
            gamepad_hub = make_pad_hub()
        if gamepad_hub is not None:  # SDL запускает сам поток опроса, не поток Qt (на Mac иначе падение)
            threads: list[GamepadThread] = []
            for i in range(4):
                def make_thread(mapper, on_intents, on_conn, on_batt, i=i):
                    t = GamepadThread(gamepad_hub.backend(i), mapper, on_intents, on_conn, on_battery=on_batt)
                    threads.append(t)
                    return t
                self._bridges.append(GamepadBridge(menu_open=self._menu_is_open, parent=self, driver=make_thread))
            self._pad_multi = MultiPadThread(gamepad_hub, threads)
            self._pad_multi.start()
        else:
            self._bridges.append(GamepadBridge(gamepad_backend if gamepad_backend is not None else make_pad_backend(),
                                               menu_open=self._menu_is_open, parent=self))
        self.pad = self._bridges[0]
        for b in self._bridges:
            b.intents.connect(lambda its, b=b: self._on_pad_intents(its, self._pad_nums.get(b), b))
            b.connectionChanged.connect(lambda info, b=b: self._on_pad_connection(info, b))
            b.batteryChanged.connect(self._update_pad_chip)
        self._last_waist = "waist_center"  # куда вернуться в пояс по L1 из «Роста»

        self.controls = ManualControls()
        self.follow_panel = FollowPanel()

    def _build_top_bar(self):
        """Верхняя строка; возвращает меню видеопотока для ⋯ клетки."""

        # верхняя строка: знак; то, что важно в эфире (ATEM, геймпады, журнал, агент); меню ☰; TRACK.
        # Всё про одну камеру — в шапке её клетки; видео и анализ выбранной — внизу слева
        self.cameras_menu = cam_menu = QMenu("Камеры", self)
        self.add_action = cam_menu.addAction("Добавить камеру…", self._add_camera)
        self.edit_action = cam_menu.addAction("Изменить камеру…", self._edit_camera)
        self.del_action = cam_menu.addAction("Удалить камеру…", self._delete_camera)

        self.video_pill = StatusPill("screen")
        self.video_pill.setPopupMode(QToolButton.InstantPopup)
        stream_menu = QMenu(self)
        self.stream_actions = QActionGroup(self)
        self.stream_actions.setExclusive(True)
        for key, label in STREAM_CHOICES.items():
            act = stream_menu.addAction(label)
            act.setCheckable(True)
            act.setData(key)
            self.stream_actions.addAction(act)
        self.stream_actions.triggered.connect(lambda act: self._on_stream_changed(act.data()))
        self.video_pill.setMenu(stream_menu)
        self.analysis_pill = StatusPill("chip")
        self.atem_pill = StatusPill("atem")
        self.atem_pill.hide()
        self.atem_pill.clicked.connect(self._edit_atem)
        self.hub_pill = StatusPill("hub")  # Videohub: что на выходе; нет хаба — нет значка
        self.hub_pill.hide()
        self.hub_pill.clicked.connect(self._edit_videohub)
        self.blackbox_btn = StatusPill("history")
        self.blackbox_btn.setPopupMode(QToolButton.InstantPopup)
        self.agent_badge = StatusPill("robot", "AGENT", "accent")
        self.agent_badge.hide()
        # геймпады: значок каждого подключённого — номер и заряд; подробности в подсказке
        self.top_pads = QWidget()
        tp = QHBoxLayout(self.top_pads)
        tp.setContentsMargins(0, 0, 0, 0)
        tp.setSpacing(SP[0])
        self.top_pad_badges = [PadBadge(framed=False) for _ in range(4)]
        for b in self.top_pad_badges:
            tp.addWidget(b)
            b.setCursor(Qt.PointingHandCursor)
            b.mousePressEvent = lambda _e: self.show_gamepads()
            b.hide()
        self.top_pads.hide()
        self.hotkeys_dialog: HotkeysDialog | None = None
        self.gamepads_dialog: GamepadsDialog | None = None
        self.main_menu_btn = self._menu_button("menu", "Меню")
        self.track_btn = self.follow_panel.follow_btn

        self.toolbar = QToolBar("Камера")
        self.toolbar.setMovable(False)
        self.toolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.toolbar.setFloatable(False)
        self._top = QWidget()
        self._top.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # ширину держит _fit_top_bar
        tl = QHBoxLayout(self._top)
        tl.setContentsMargins(SP[1], SP[0], SP[1], SP[0])
        tl.setSpacing(SP[1])
        tl.addWidget(BrandMark(24))
        brand = QLabel("PTZ <span style='font-weight:400; letter-spacing:3px; font-size:10px;'>TRACKER</span>")
        brand.setStyleSheet(f"color: {C.TEXT}; font-weight: 800; font-size: 14px; letter-spacing: 0.5px;")
        tl.addWidget(brand)
        tl.addStretch(1)
        # «4 камеры» / «1 камера» (клавиша G, двойной клик по шапке клетки)
        self.view_grid_btn = QToolButton()
        self.view_one_btn = QToolButton()
        seg = QWidget()
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        for b, text, tip, side in ((self.view_grid_btn, "▦ 4", "Сетка: до 4 камер (G)", "left"),
                                   (self.view_one_btn, "▢ 1", "Одна камера крупно (G)", "right")):
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QToolButton {{ background: transparent; color: {C.TEXT_DIM}; border: 1px solid {C.LINE};"
                f" border-top-{side}-radius: {R_BUTTON}px; border-bottom-{side}-radius: {R_BUTTON}px;"
                f" padding: 2px {SP[1]}px; font-size: {FS_CAPTION}px; font-weight: 600; }}"
                f"QToolButton:hover {{ background: {C.RAISED}; }}"
                f"QToolButton:checked {{ background: {C.TEXT}; color: {C.BG}; border-color: {C.TEXT}; }}")
            sl.addWidget(b)
        self.view_grid_btn.setChecked(True)
        self.view_grid_btn.clicked.connect(lambda: self.set_single_view(False))
        self.view_one_btn.clicked.connect(lambda: self.set_single_view(True))
        tl.addWidget(seg)
        tl.addSpacing(SP[1])
        for w in (self.atem_pill, self.hub_pill, self.top_pads, self.blackbox_btn, self.agent_badge):
            tl.addWidget(w)
        tl.addWidget(_vline())
        tl.addWidget(self.main_menu_btn)
        tl.addSpacing(SP[1])
        tl.addWidget(self.track_btn.action_btn)
        tl.addWidget(self.track_btn)
        self.toolbar.addWidget(self._top)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)

        for c in self.cells:
            c.preview.setMinimumSize(240, 135)  # четыре превью в одном окне
        self.camera_menu = CameraMenu(self._menu_session, self.controls)
        return stream_menu

    def _build_side_panel(self):
        """Правая колонка и плашки поверх видео."""
        # виртуальную камеру нельзя принять за живую: плашка «СИМУЛЯТОР» в шапке клетки
        self._tally_per: list[tuple[int, str | None, bool]] = [(0, None, False)] * GRID_SLOTS
        self.camera_menu.openChanged.connect(self._on_menu_changed)
        # меню Panasonic камера рисует в своё видео: у камеры в эфире — спросить перед открытием
        self.camera_menu.on_air = lambda: (s := self._menu_slot()) is not None and self._tally_per[s][1] == "pgm"
        self.camera_menu.on_power_on = lambda: self._power_on(self._menu_slot())  # Options → «Включить камеру»
        self.camera_menu.placementChanged.connect(
            lambda corner: self._menu_host is not None and self._menu_host.set_overlay_corner(self.camera_menu, corner))

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(SP[1], 0, SP[1], 0)
        sl.setSpacing(SP[1])
        # чем сейчас управляет правая колонка: номер и имя выбранной камеры, её эфир
        head = QHBoxLayout()
        head.setSpacing(SP[1])
        self.side_num = QLabel("")
        self.side_num.setFixedSize(22, 22)
        self.side_num.setAlignment(Qt.AlignCenter)
        self.side_num.setStyleSheet(f"background: {C.TEXT}; color: {C.BG}; border-radius: 6px; font-weight: 700;"
                                    f" font-size: {FS_CAPTION}px;")
        self.side_name = QLabel("")
        self.side_name.setStyleSheet(f"color: {C.TEXT}; font-weight: 600;")
        self.side_tally = StateTag("pgm")
        head.addWidget(self.side_num)
        head.addWidget(self.side_name, 1)
        head.addWidget(self.side_tally)
        sl.addLayout(head)
        self.follow_panel.title_header.hide()  # силуэты говорят сами за себя; место — под имя камеры
        sl.addWidget(self.follow_panel)
        sl.addWidget(self.controls, 1)  # лишняя высота — внутри, поровну перед «Камерой» и «Изображением»
        # агент — в немодальном окне из меню ☰ (модальное заблокировало бы API: 409)
        self.agent_panel = AgentPanel()  # разрешения агенту: по умолчанию выключены, не сохраняются
        self.agent_window = QDialog(self)
        self.agent_window.setWindowTitle("Агент")
        self.agent_window.setModal(False)
        self.agent_window.resize(560, 560)
        QVBoxLayout(self.agent_window).addWidget(self.agent_panel)
        self.agent_panel.attach(self.grid)
        self.agent_panel.anyAllowedChanged.connect(self.agent_badge.setVisible)  # хоть у одной камеры
        self.agent_badge.clicked.connect(self._show_agent_window)
        # пояснение слежения и меню камеры — поверх видео выбранной клетки (переносятся при выборе)
        self._overlay_host: VideoFrame | None = None

        # боковая панель прокручивается — окно помещается и на небольших экранах
        side_scroll = self.side_scroll = QScrollArea()
        side_scroll.setWidget(side)
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side_scroll.setFrameShape(QScrollArea.NoFrame)

        # «СТОП КАМЕРЫ» закреплён под прокруткой: аварийная кнопка всегда на виду
        self.controls.layout().removeWidget(self.controls.stop_btn)
        side_col = QWidget()
        side_col.setFixedWidth(SIDE_WIDTH)
        cl = QVBoxLayout(side_col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SP[0])
        cl.addWidget(side_scroll, 1)
        stop_row = QHBoxLayout()
        stop_row.setContentsMargins(SP[1], 0, SP[1], SP[0])  # стиль делает кнопку выше H_STOP на рамку
        stop_row.addWidget(self.controls.stop_btn)
        cl.addLayout(stop_row)

        grid_host = self.grid_host = QWidget()
        gl = QGridLayout(grid_host)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(SP[0])
        for i, c in enumerate(self.cells):
            gl.addWidget(c, i // 2, i % 2)
            gl.setRowStretch(i // 2, 1)
            gl.setColumnStretch(i % 2, 1)
            c.selectRequested.connect(self.select_slot)
            c.cameraChosen.connect(self._on_cell_chosen)
            c.power_overlay.powerOnRequested.connect(lambda s=i: self._power_on(s))
            c.reset_target_btn.clicked.connect(lambda _=False, s=i: self._reset_target(s))
            c.skeleton_check.toggled.connect(lambda on, cell=c: self._on_skeleton(on, cell))
            c.preview.targetClicked.connect(lambda _id: self._update_status())
            c.preview.zoneChanged.connect(lambda x, y, k, s=i: self._on_zone(s, Zone(x, y, k)))
            c.preview.zoneCommitted.connect(lambda s=i: self._save_zone(s))
            c.preview.zoneReset.connect(lambda s=i: self._on_zone(s, None, save=True))
            c.preview.zone_scale_of = lambda s=i: self._zone_scale(s)
            c.preview.zoneHoverChanged.connect(lambda _h: self._update_hint_context())
            c.analysisRequested.connect(self.request_analysis)
            c.preview.mouseVector.connect(lambda x, y, s=i: self._on_mouse_vector(s, x, y))
            c.preview.wheelStep.connect(lambda d, s=i: self._on_mouse_wheel(s, d))
            c.preview.rightClicked.connect(lambda s=i: self._on_mouse_right(s))
            c.preview.leftClicked.connect(lambda s=i: self._on_mouse_left(s))
            c.preview.doubleClicked.connect(lambda s=i: self.enter_mouse_mode() if s == self.grid.selected else None)
            c.powerClicked.connect(self._cell_power)
            c.viewToggleRequested.connect(self._toggle_view_at)
        return grid_host, side_col

    def _build_central(self, store, grid_host, side_col):
        """Центр окна: сетка, панель пресетов, полоска «1 камера», правая колонка."""
        # пресеты — выдвижная панель под сеткой камер (кнопка «Пресеты» внизу или P); правая колонка не двигается
        self.thumbs = PresetThumbs(store.path.parent / "presets")
        self._thumb_cache: dict[str, dict] = {}
        self._last_preset: dict[int, int] = {}
        self._thumb_watch: dict[int, tuple[int, int, tuple | None]] = {}  # клетка → (пресет, тиков, положение)
        self.presets_drawer = PresetsDrawer()
        self.presets_drawer.hide()
        self.presets_drawer.recallRequested.connect(self.recall_preset)
        self.presets_drawer.storeRequested.connect(self.store_preset)
        self.presets_drawer.eraseRequested.connect(self._erase_preset)
        self.presets_drawer.rowsShownChanged.connect(lambda _n: self._fit_drawer_height())
        self.presets_drawer.rowsOrderChanged.connect(self._on_preset_rows)
        self.presets_drawer.speedChanged.connect(lambda n: self.session and self.session.set_preset_speed(n))
        left = QWidget()
        left.setObjectName("leftCol")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(SP[1])
        ll.addWidget(grid_host, 1)
        # режим «1 камера»: остальные камеры — полоской под большой (клик — сделать большой)
        self.single_view = False
        self._timelines: dict[str, StateTimeline] = {}  # камера → лента состояний для «Сводки»
        self._service_pref: dict[int, str] = {}  # клетка → что в ней показывать (выбор оператора в её списке)
        self._cycle_kind = CYCLE_KINDS[0]
        self._cycle_started = time.monotonic()
        self.strip = CameraStrip()
        self.strip.hide()
        self._single_slot: int | None = None  # «1 камера»: крупно служебный вид из этой клетки (None — камера)
        self.strip.slotClicked.connect(self._strip_clicked)
        ll.addWidget(self.strip)
        ll.addWidget(self.presets_drawer)
        central = QWidget()
        hl = QHBoxLayout(central)
        hl.addWidget(left, 1)
        hl.addWidget(side_col)
        self.setCentralWidget(central)

    def _build_status_bar(self):
        """Нижняя строка."""

        # строка состояния: слева сообщения; справа видео и анализ выбранной камеры | Пресеты, Справка | версия.
        # Подписей клавиш нет — всё в справке F1
        sb = MessageStatusBar()
        sb.setContentsMargins(SP[1], 0, 0, 0)  # скруглённая строка записи не упирается в край окна
        self.setStatusBar(sb)
        sb.setSizeGripEnabled(False)
        self.presets_btn = KeyButton("Пресеты", "P")
        self.presets_btn.setCheckable(True)
        self.help_btn = KeyButton("Справка", "F1")
        for b in (self.presets_btn, self.help_btn):
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet(f"QPushButton {{ min-height: 24px; max-height: 24px; padding: 0 {SP[1]}px;"
                            f" font-size: {FS_CAPTION}px; border-radius: 6px; text-align: left; }}")
        self.help_btn.clicked.connect(self.show_hotkeys)
        self.presets_btn.toggled.connect(self._show_presets)
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        self.credit_label = QLabel("by funpin.ru & Claude")
        self.credit_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        # отступ справа: в окне со скруглёнными углами подпись не упирается в край
        self.credit_label.setContentsMargins(0, 0, SP[2], 0)
        self.load_meter = LoadMeter()
        for wd in (self.video_pill, self.analysis_pill, self.load_meter, _vline(), self.presets_btn, self.help_btn, _vline(),
                   self.version_label, self.credit_label):
            sb.addPermanentWidget(wd)
        # чёрный ящик: «Показать в папке» после F9 — слева в строке состояния
        self.show_folder_btn = QPushButton("Показать в папке")
        self.show_folder_btn.setProperty("variant", "ghost")
        self.show_folder_btn.hide()
        self.rec_pill = RecordPill()
        self.rec_pill.stop_requested.connect(self.stop_video_slot)
        self.rec_pill.stop_all_requested.connect(self.stop_all_video)
        self.rec_pill.open_folder_requested.connect(self._open_video_folder)
        sb.addWidget(self.rec_pill)
        sb.addWidget(self.show_folder_btn)
        sb.attach_message_label()

    def _build_menus(self, stream_menu):
        """Действия и меню ☰."""

        # одно меню ☰ в окне — одинаково на Mac и Windows; меню «Сервис» в строке macOS нет.
        # Строки ☰ — заместители этих действий (ui/main_menu.py): группы, значки, состояние справа, без подменю
        menu = self.main_menu_btn.menu()
        # про одну камеру — в ⋯ её клетки (спека «Меню ☰ (только общее)»); действия общие для всех клеток
        self.calib_action = QAction("Калибровка камеры…", self)
        self.calib_action.triggered.connect(self._run_calibration)
        self.diag_action = QAction("Диагностика камеры…", self)
        self.diag_action.triggered.connect(self._run_diagnostics)
        self.power_action = QAction("Перевести камеру в режим ожидания…", self)
        self.power_action.triggered.connect(self._power_menu)
        self.osd_action = QAction("Быстрые настройки\tM", self)
        self.osd_action.triggered.connect(self._open_osd_menu)
        self.atem_action = QAction("ATEM…", self)
        self.atem_action.triggered.connect(self._edit_atem)
        self.videohub_action = QAction("Videohub…", self)
        self.videohub_action.triggered.connect(self._edit_videohub)
        self.agent_action = QAction("Агент…", self)
        self.agent_action.triggered.connect(self._show_agent_window)
        self.pads_action = QAction("Геймпады…", self)
        self.pads_action.triggered.connect(self.show_gamepads)
        self.sim_action = QAction("Симулятор для тренировки", self)
        self.sim_action.triggered.connect(self._open_sim_camera)
        self.save_moment_action = QAction("Сохранить последние 5 минут\tF9", self)  # не ручное управление: слежение не трогаем
        self.save_moment_action.triggered.connect(lambda: self.save_moment())
        self.open_replay_action = QAction("Открыть журнал…", self)
        self.open_replay_action.triggered.connect(self._choose_replay)
        self.rec_settings_action = QAction("Настройки журнала…", self)
        self.rec_settings_action.triggered.connect(self._recording_settings)
        self.video_settings_action = QAction("Запись видео…", self)
        self.video_settings_action.triggered.connect(self._video_settings)
        box_menu = QMenu(self)
        for act in (self.save_moment_action, self.open_replay_action, self.rec_settings_action):
            box_menu.addAction(act)
        self.blackbox_btn.setMenu(box_menu)
        self.check_updates_action = QAction("Проверять обновления", self)
        self.check_updates_action.setCheckable(True)
        self.check_updates_action.setChecked(self.cfg.check_updates)
        self.check_updates_action.toggled.connect(self._on_check_updates_toggled)
        self.analyze_action = QAction("Камер с анализом", self)
        self.max_analyzed_group = QActionGroup(self)
        self.max_analyzed_group.setExclusive(True)
        self.max_analyzed_actions: list[QAction] = []
        for n in range(1, GRID_SLOTS + 1):
            act = QAction(str(n), self)
            act.setCheckable(True)
            act.setData(n)
            act.setChecked(n == self.cfg.max_analyzed)
            self.max_analyzed_group.addAction(act)
            self.max_analyzed_actions.append(act)
        self.max_analyzed_group.triggered.connect(lambda act: self._on_max_analyzed(act.data()))
        self.update_action = QAction("", self)
        self.update_action.triggered.connect(self._open_release_page)
        self.update_action.setVisible(False)
        self.help_action = QAction("Справка и клавиши\tF1", self)
        self.help_action.triggered.connect(self.show_hotkeys)
        mm = self.main_menu = MainMenu(menu)
        mm.add(self.update_action, "refresh")
        mm.section("Камеры", first=True)
        mm.add(self.add_action, "plus")
        mm.add(self.sim_action, "video")
        mm.add(self.del_action, "trash")
        mm.section("Подключения")
        mm.add(self.atem_action, "atem", self._atem_menu_status)
        mm.add(self.videohub_action, "hub", self._videohub_menu_status)
        mm.add(self.pads_action, "gamepad", self._pads_menu_status)
        mm.add(self.agent_action, "robot", self._agent_menu_status)
        mm.section("Запись видео")
        mm.add(self.video_settings_action, "folder")
        mm.section("Журнал для разбора")
        mm.add(self.save_moment_action, "record")
        mm.add(self.open_replay_action, "folder")
        mm.add(self.rec_settings_action, "adjustments")
        mm.section("Программа")
        mm.add(self.analyze_action, "chip", choices=self.max_analyzed_actions)
        mm.add(self.check_updates_action, "refresh")
        mm.add(self.help_action, "help")
        for i, c in enumerate(self.cells):
            self._build_cell_menu(i, c.more_btn.menu(), stream_menu)
        self._saveFinished.connect(self._on_save_finished)
        self._saved_path: Path | None = None
        self._hide_folder_timer = QTimer(self)
        self._hide_folder_timer.setSingleShot(True)
        self._hide_folder_timer.timeout.connect(self.show_folder_btn.hide)
        self.show_folder_btn.clicked.connect(self._show_saved_folder)

        self.follow_panel.followRequested.connect(self._on_follow_requested)
        self.track_btn.camToggled.connect(lambda n: self._toggle_follow_slot(n - 1))
        self.follow_panel.calibrateRequested.connect(self._run_calibration)
        self.controls.stopRequested.connect(self.emergency_stop)
        self.controls.set_sensitivity(self.cfg.stick_sensitivity, self.cfg.slow_on_tele)
        self.controls.sensitivityChanged.connect(self._on_stick_sensitivity)
        self.controls.slowTeleChanged.connect(self._on_slow_tele)
        self.follow_panel.compositionChanged.connect(self._on_composition)
        self.follow_panel.effortChanged.connect(self._on_effort)
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._sync_follow)
        self._follow_timer.timeout.connect(self._sync_tally)
        self._follow_timer.timeout.connect(self._sync_pad)
        self._follow_timer.start(100)
        self._view_shown: TrackingView | None = None

    def _start_services(self):
        """API агента, таймеры, фоновые службы."""
        # API агента: оператор сам включил/выключил слежение (пробел, кнопка) — слежение теперь его
        self.on_operator_follow: Callable[[bool], None] | None = None

        self._speed_scale_saved_t = float("-inf")
        self._recalib_hinted: set[str] = set()  # камеры, которым подсказка «откалибруйте заново» уже показана в этот запуск
        self._recalib_hint_for: str | None = None  # камера, у которой подсказка видна в нынешнем подключении
        self._unsupported_shown: tuple = ()
        self.system_load = SystemLoad()
        self.system_load.start()
        self.video = VideoController(self.cfg, self._video_sessions)
        self.video.disk.start()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.timeout.connect(self._sync_hub)
        self._status_timer.start(500)
        # снимки камер в полоске «1 камера» — чаще тика статуса; таймер идёт только в этом режиме
        self._strip_timer = QTimer(self)
        self._strip_timer.setInterval(STRIP_MS)
        self._strip_timer.timeout.connect(self._refresh_strip_frames)

        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)
        self._restart_atem()
        self._restart_videohub()
        self._initial_grid()
        self._api = start_window_api(self)
        QTimer.singleShot(3000, self._start_update_check)

    # --- выбранная клетка ----------------------------------------------


    # --- список камер и клетки ------------------------------------------

    def _cameras(self) -> list[CameraConfig]:
        # --sim добавляет временные камеры; если такая уже в настройках с тем же id, второй не нужно
        stored = {c.id for c in self.cfg.cameras}
        return self.cfg.cameras + [c for c in self._sim_cams if c.id not in stored]


    def _open_sim_camera(self) -> None:
        """☰ → «Симулятор (для тренировки)»: добавить виртуальную камеру (один раз) и открыть её —
        в её клетке, если она уже открыта; иначе в первой пустой клетке, а без пустых — в выбранной."""
        sim = next((c for c in self.cfg.cameras if c.kind == "sim"), None)
        if sim is None:
            sim = dataclasses.replace(SIM_CAMERA, name="Тренировка")
            self.cfg.cameras.append(sim)
            self._save()
        slot = self.grid.slot_of(sim.id)
        if slot is None:
            empty = [i for i, c in enumerate(self.grid.cells) if c is None]
            slot = empty[0] if empty else (self.grid.selected or 0)
            self.set_cell_camera(slot, sim.id)
        self.select_slot(slot)
        self._refresh_cell_choices()


    # --- слежение -----------------------------------------------------


    # --- для API агента (вызывать в потоке Qt) ---------------------------


    # --- новая версия -------------------------------------------------


    # --- пресеты: выдвижная панель и снимки ---------------------------


    def _save(self) -> None:
        try:
            self.store.save(self.cfg)
        except OSError as e:
            log.error("Не удалось сохранить настройки: %s", e)
            self.statusBar().showMessage(f"Не удалось сохранить настройки: {e}", 5000)

    def _add_camera(self) -> None:
        d = CameraDialog(parent=self)
        if d.exec():
            cam = d.result_config()
            if cam.kind != "sim":
                set_password(cam.id, d.password())
            self.cfg.cameras.append(cam)
            self._save()
            slot = self.grid.selected
            if self.grid.cells[slot] is not None:  # занятую клетку не трогаем, если есть свободная
                slot = next((i for i, c in enumerate(self.grid.cells) if c is None), slot)
            self.select_slot(slot)
            self._open_cell(slot, cam)

    def _edit_camera(self) -> None:
        cur = self._current_cfg()
        if not self._is_stored(cur):
            return
        d = CameraDialog(cur, get_password(cur.id) if cur.username else None, parent=self)
        if d.exec():
            result = d.result_config()
            if result.kind != "sim":
                set_password(result.id, d.password())
            # пока диалог был открыт, окно могло узнать модель или сохранить поправку скорости: берём свежую запись
            # и меняем в ней только то, что оператор тронул
            latest = next((c for c in self.cfg.cameras if c.id == result.id), None)
            cam = merge_edit(latest, d.snapshot, result, d.reset_calibration) if latest is not None else result
            self.cfg.cameras = [cam if c.id == cam.id else c for c in self.cfg.cameras]
            self._save()
            slot = self.grid.slot_of(cam.id)
            if slot is not None:
                self._open_cell(slot, cam)  # клетка открывается заново с новыми настройками
            else:
                self._refresh_cell_choices()

    def _delete_camera(self) -> None:
        cur = self._current_cfg()
        if not self._is_stored(cur):
            return
        if QMessageBox.question(self, "Удалить камеру", f"Удалить «{cur.name}» из списка?") != QMessageBox.Yes:
            return
        slot = self.grid.slot_of(cur.id)
        if slot is not None:
            self._open_cell(slot, None)  # сначала клетка «нет»
        self.cfg.cameras = [c for c in self.cfg.cameras if c.id != cur.id]
        if cur.kind != "sim":
            set_password(cur.id, None)
        self._save()
        self._refresh_cell_choices()
        self._save_grid()

    def _run_calibration(self) -> None:
        from ptztracker.control.calibration import CalibrationRunner
        from ptztracker.ui.calibration_dialog import CalibrationDialog

        slot, cell = self.grid.selected, self.selected_cell  # камера захвачена при открытии диалога
        if cell is None:
            return
        cam, s = cell.cfg, cell.session
        if s.follower:
            s.follower.disable("калибровка")
        kind = s.stream_kind()

        def factory(progress, cancel=None, on_sample=None):
            s.quiet_for_calibration()  # опросы положения и пульс сбивают команды мастера (eR2)
            return CalibrationRunner(
                s.client.send, s.frames, progress, stream_kind=kind,
                model=getattr(s, "model", None), firmware=getattr(s, "firmware", None),  # поля сессии — с задачи 2
                cancel=cancel, on_sample=on_sample,
            )

        def saved(calib: dict) -> None:
            if cam.kind == "sim":  # симулятору калибровка не нужна: в настройки не пишем
                return
            cam.calibration = calib
            cam.speed_scale = None  # новая таблица скоростей: прежняя поправка к ней не относится
            self._save()

        dlg = CalibrationDialog(factory, saved, parent=self)
        dlg.setWindowTitle(f"{dlg.windowTitle()} — {cam.name}")
        self._cell_dialogs[slot] = dlg
        try:
            dlg.exec()
        finally:
            if self._cell_dialogs.get(slot) is dlg:
                del self._cell_dialogs[slot]
        if self.grid.cells[slot] is not cell:
            return  # клетку закрыли или сменили камеру, пока шла калибровка
        if cam.calibration is not None:
            self._open_cell(slot, cam)  # слежение подхватит новую калибровку
        else:
            s.resume_after_calibration()

    def _run_diagnostics(self) -> None:
        from ptztracker.ui.diagnostics_dialog import DiagnosticsDialog

        slot = self.grid.selected
        cam = self._current_cfg()
        if not cam or cam.kind == "sim":
            QMessageBox.information(self, "Диагностика", "Выберите реальную камеру из списка.")
            return
        # на время диагностики программа не управляет камерой
        self._open_cell(slot, None)
        password = get_password(cam.id) if cam.username else None
        dlg = DiagnosticsDialog(cam.ip, cam.username, password, parent=self, name=cam.name)
        dlg.setWindowTitle(f"{dlg.windowTitle()} — {cam.name}")
        dlg.exec()
        if self.grid.cells[slot] is None and self.grid.slot_of(cam.id) is None:
            self._open_cell(slot, cam)

    # --- запись (чёрный ящик) ------------------------------------------


    # --- статус -------------------------------------------------------


    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit_later()
        self._fit_top_bar()


    # --- горячие клавиши ---------------------------------------------


    def eventFilter(self, obj, event):  # noqa: N802
        et = event.type()
        if et in _KB_MOUSE_EVENTS or (et == QEvent.MouseMove and isinstance(obj, PreviewWidget)):
            if self._is_mine(obj):  # мышь, проехавшая мимо видео, подсказку не переключает
                self._note_input("kb")
        if et in (QEvent.KeyPress, QEvent.KeyRelease) and self._is_mine(obj):
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QAbstractSpinBox)):
                return False
            key = logical_key(event.key())
            if key == Qt.Key_Shift:  # Shift + стрелки / WASD — поворот быстрее
                self._fast = et == QEvent.KeyPress
                if self._held:
                    self._apply_held()
                return False
            digit = digit_of(event.key(), event.nativeVirtualKey())
            if digit is not None and not self.camera_menu.is_open:  # 1–9 — пресет, Shift + цифра — записать
                if et == QEvent.KeyPress and not event.isAutoRepeat() and self.grid.selected is not None:
                    if event.modifiers() & Qt.ShiftModifier:
                        self.store_preset(self.grid.selected, digit)
                    else:
                        self.recall_preset(self.grid.selected, digit)
                return True
            key = _WASD.get(key, key)
            if key in (Qt.Key_Tab, Qt.Key_Backtab):  # Tab — режим мыши вкл / выкл
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.exit_mouse_mode() if self.mouse_slot is not None else self.enter_mouse_mode()
                return True
            if key == Qt.Key_Space:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.toggle_follow()
                return True
            comp_keys = dict(zip((Qt.Key_Z, Qt.Key_X, Qt.Key_C), SIZES, strict=True))  # Z пояс, X рост, C общий
            if key in comp_keys:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self._on_composition(comp_keys[key])
                return True
            if key == Qt.Key_F1:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.show_hotkeys()
                return True
            if key == Qt.Key_P:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.presets_btn.toggle()
                return True
            if key == Qt.Key_R:  # запись видео: не ручное действие, флажки и слежение не трогает
                if et == QEvent.KeyPress and not event.isAutoRepeat() and self.grid.selected is not None:
                    self.toggle_video(self.grid.selected)
                return True
            if key == Qt.Key_G:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.set_single_view(not self.single_view)
                return True
            if key == Qt.Key_F9:  # сохранить момент: не ручное управление, слежение не трогаем
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.save_moment()
                return True
            if key == Qt.Key_Escape and et == QEvent.KeyPress:
                self.emergency_stop()
                return True
            if key == Qt.Key_M:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    if not self.camera_menu.is_open:
                        self.release_all_held()
                    self.camera_menu.pad_hints = False  # открыли клавишей или мышью — клавиши
                    self.camera_menu.toggle()
                return True
            if self.camera_menu.is_open and key in _MENU_KEYS:
                if not event.isAutoRepeat():
                    self._menu_key(key, et == QEvent.KeyPress)
                return True
            if key in _PAN_KEYS | _ZOOM_IN_KEYS | _ZOOM_OUT_KEYS:
                if not event.isAutoRepeat():
                    if et == QEvent.KeyPress:
                        self._held.add(key)
                    else:
                        self._held.discard(key)
                    self._apply_held()
                return True
        return super().eventFilter(obj, event)

    # --- режим «камера как курсор» (план шага Б, часть 3; макет mouse-mode.html) -----------------

    MOUSE_WHEEL_ZOOM = 0.6  # скорость зума от одного щелчка колеса…
    MOUSE_WHEEL_MS = 180  # …столько миллисекунд
    MOUSE_IRIS_STEP = 4  # шаг ириса колесом, % ползунка


    # --- чувствительность ручного управления ---------------------------


    # --- геймпад ------------------------------------------------------


    # --- точки входа геймпадов (поток Qt); мост зовёт их для своего номера в маршрутизаторе ---


    def changeEvent(self, e):  # noqa: N802
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.camera_menu.stop_focus()
            self.release_all_held()
        super().changeEvent(e)


    def closeEvent(self, e):  # noqa: N802
        if self.video.any_active() and not self._confirm_close_with_video():
            e.ignore()
            return
        recorders = self.video.stop_all()
        try:
            self._prepare_close()
        except Exception:  # сбой подготовки не отменяет стоп камер
            log.exception("Закрытие окна: подготовка прервалась")
        self.grid.close_all()
        self.video.wait_closed(recorders)  # после стопа камер: файлы не должны задерживать стоп
        self._save()
        super().closeEvent(e)

    def _prepare_close(self) -> None:
        self.system_load.stop()
        self.video.disk.stop()
        if self._api:
            self._api.stop()
            self._api = None
        QApplication.instance().removeEventFilter(self)
        if not getattr(self, "_app_state_off", False):  # окно закрывают и второй раз — отключить один раз
            self._app_state_off = True
            QApplication.instance().applicationStateChanged.disconnect(self._on_app_state)
        self._status_timer.stop()
        self._hide_folder_timer.stop()
        self._follow_timer.stop()
        if self.atem:
            self.atem.stop()
            self.atem = None
        self._stop_videohub()
        for b in self._bridges:
            b.stop(timeout=0.1)  # до остановки сессии; Qt не ждёт SDL: зависший поток доделает выход сам
        if self._pad_multi is not None:
            self._pad_multi.stop(timeout=0.1)
        self._pad_stop_motion()
        self.release_all_held()
        for d in list(self._cell_dialogs.values()):
            d.reject()
