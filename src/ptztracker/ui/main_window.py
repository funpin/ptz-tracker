from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QFrame,
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
from ptztracker.camera.session import CameraSession
from ptztracker.config import (
    DEFAULT_GEAR,
    GRID_SLOTS,
    SPEED_GEARS,
    AppConfig,
    CameraConfig,
    ConfigStore,
    get_password,
    set_password,
)
from ptztracker.control.camera_model import DEFAULT_TABLE_ID
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.manual_curve import (
    DEFAULT_SENS,
    PAD_GEAR_DEG_S,
    clamp_sens,
    gear_of,
    shape,
    tele_scale,
)
from ptztracker.gamepad.device import GamepadThread, MultiPadThread, SdlBackend, SdlHub
from ptztracker.gamepad.feedback import (
    RUMBLE_EDGE,
    RUMBLE_NOWHERE,
    RUMBLE_PRESET_STORED,
    RUMBLE_STEP,
    RUMBLE_TAKEN,
    PadStatus,
)
from ptztracker.gamepad.intents import (
    Composition,
    Disconnected,
    GainStep,
    IrisStep,
    MenuAccept,
    MenuClose,
    MenuNav,
    MenuOpen,
    Neighbor,
    NextCamera,
    PanTilt,
    Preset,
    PushFocus,
    SaveMoment,
    SpeedStep,
    Stop,
    ToggleFollow,
    Zoom,
)
from ptztracker.multicam.analysis import CellState, allocate_analysis, tracking_refusal
from ptztracker.multicam.grid import CameraGrid, Cell
from ptztracker.multicam.pads import PadFeedback, PadMove, PadRouter
from ptztracker.multicam.velocity import MixerSource
from ptztracker.tally import atem_input_of
from ptztracker.tally.atem import AtemTally
from ptztracker.ui.agent_panel import AgentPanel, revokes_agent
from ptztracker.ui.atem_dialog import AtemDialog
from ptztracker.ui.camera_cell import CameraCell
from ptztracker.ui.camera_dialog import CameraDialog, merge_edit
from ptztracker.ui.camera_menu import CameraMenu
from ptztracker.ui.follow_panel import FollowPanel
from ptztracker.ui.gamepad_bridge import GamepadBridge
from ptztracker.ui.gamepads_dialog import GamepadsDialog, PadRow
from ptztracker.ui.hotkeys_dialog import HotkeysDialog, fmt_remaining
from ptztracker.ui.manual_controls import ManualControls
from ptztracker.ui.service_cell import KINDS, ServiceCell, ServiceRow
from ptztracker.ui.state import (
    TrackingView,
    analysis_pill,
    atem_pill,
    power_view,
    recalibration_hint,
    tracking_view,
    video_pill,
)
from ptztracker.ui.theme import FS_CAPTION, SP, C
from ptztracker.ui.widgets import StatusPill, VideoFrame, keycap_width, paint_keycap
from ptztracker.updates import CHECK_INTERVAL_S, RELEASE_PAGE, UpdateChecker, UpdateInfo, is_newer
from ptztracker.video.rtsp import STREAM_CHOICES, strip_credentials

log = logging.getLogger(__name__)

SPEED_SCALE_SAVE_S = 60.0  # поправку скорости от слежения пишем в настройки не чаще раза в минуту
SPEED_SCALE_SAVE_DIFF = 0.01  # меньшее изменение поправки не сохраняем

SIM_CAMERA = CameraConfig(id="sim", name="Симулятор", ip="sim", kind="sim")  # образец для sim1…sim4


def sim_cameras() -> list[CameraConfig]:
    """Четыре виртуальные камеры для --sim: sim1…sim4, «Симулятор 1…4»."""
    return [dataclasses.replace(SIM_CAMERA, id=f"sim{i}", name=f"Симулятор {i}") for i in range(1, GRID_SLOTS + 1)]


KEY_PAN_SPEED = 0.4  # до кривой чувствительности: «Резко» — как в 1.0.1
ZOOM_POLL_S = 1.0  # «медленнее на крупном плане»: #GZ не чаще раза в секунду на камеру (слежение спрашивает само)
KEY_ZOOM_SPEED = 0.5
SIDE_WIDTH = 372
_HOTKEYS = [
    ("Пробел", "слежение"), ("1–5", "кадр"), ("← → ↑ ↓", "поворот"), ("+ −", "зум"), ("Esc", "стоп"),
    ("F9", "сохранить момент"), ("M", "меню камеры"),
]  # F1 («справка») сюда не добавляем: окно F1 показывает её само
# в строке состояния — только главное; остальное в окне F1
_FOOTER_KEYS = [("Пробел", "слежение"), ("Esc", "стоп"), ("F9", "сохранить момент"), ("F1", "все клавиши")]
_PAN_KEYS = {Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down}
_MENU_KEYS = {Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter}
_ZOOM_IN_KEYS = {Qt.Key_Plus, Qt.Key_Equal}
_ZOOM_OUT_KEYS = {Qt.Key_Minus, Qt.Key_Underscore}


def make_pad_backend():
    """Устройство геймпада по умолчанию (тесты подменяют)."""
    return SdlBackend()


def make_pad_hub():
    """Общий SDL для нескольких геймпадов (тесты подменяют)."""
    return SdlHub()


_PAD_SIZES = ("waist", "full", "wide")  # L1/R1: только крупность, от ближнего к общему


def _clamp1(v: float) -> float:
    return max(-1.0, min(1.0, v))


class _Cap(QWidget):
    """Клавиша-подсказка (только рисунок)."""

    def __init__(self, text: str):
        super().__init__()
        self._text = text
        self.setFixedSize(int(keycap_width(text)) + 1, 24)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        paint_keycap(p, self.width(), self.height() / 2, self._text)
        p.end()


def _keycap_label(text: str) -> QWidget:
    return _Cap(text)


def _vline() -> QFrame:
    """Тонкий разделитель групп в панелях."""
    f = QFrame()
    f.setFixedSize(1, 18)
    f.setStyleSheet(f"background: {C.LINE};")
    return f


class MainWindow(QMainWindow):
    _saveFinished = Signal(object)  # (пачка, Future записи); приходит из рабочего потока, обрабатывается в Qt-потоке

    def __init__(self, store: ConfigStore, use_sim: bool = False, parent=None, atem_port: int = 9910,
                 checker_factory: Callable[[str], UpdateChecker] | None = UpdateChecker, gamepad_backend=None,
                 gamepad_hub=None):
        super().__init__(parent)
        self.setWindowTitle("PTZ Tracker")
        self.resize(1640, 850)
        self.store = store
        self.cfg: AppConfig = store.load()
        self.use_sim = use_sim
        # проверка новой версии: в --sim сеть не трогаем; в тестах подставляют фальшивку
        self._checker_factory = None if use_sim and checker_factory is UpdateChecker else checker_factory
        self._checker: UpdateChecker | None = None
        self._update_info: UpdateInfo | None = None
        self._sim_cams = sim_cameras() if use_sim else []
        self.grid = CameraGrid(self._make_session)
        self.cells: list[CameraCell] = [CameraCell(i) for i in range(GRID_SLOTS)]
        self.grid.selected = 0
        self._service = {k: ServiceCell(k) for k in KINDS}  # окно владеет, клетки только показывают
        self._service_slot: dict[str, int | None] = dict.fromkeys(KINDS)
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
        self._atem_port = atem_port
        self.atem: AtemTally | None = None
        self._tally_shown: tuple | None = None
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

        # верхняя строка: камера слева; то, что важно в эфире (ATEM, чёрный ящик, геймпад, агент); меню ☰; TRACK.
        # Видео и анализ — внизу, в строке состояния
        self.cameras_btn = self._menu_button("camera", "Камеры: добавить, изменить, удалить")
        cam_menu = self.cameras_btn.menu()
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
        self.blackbox_btn = StatusPill("history")
        self.blackbox_btn.setPopupMode(QToolButton.InstantPopup)
        self.agent_badge = StatusPill("robot", "AGENT", "accent")
        self.agent_badge.hide()
        self.pad_chip = StatusPill("gamepad")
        self.pad_chip.hide()
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
        tl.addWidget(self.cameras_btn)
        tl.addStretch(1)
        for w in (self.atem_pill, self.blackbox_btn, self.pad_chip, self.agent_badge):
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
        # виртуальную камеру нельзя принять за живую: метка поверх видео каждой клетки с симулятором
        self._sim_badges: list[QLabel] = []
        for c in self.cells:
            badge = QLabel("СИМУЛЯТОР")
            badge.setStyleSheet(
                f"background: {C.REMOTE}; color: {C.BG}; border-radius: 6px; padding: 4px 12px;"
                f" font-size: {FS_CAPTION}px; font-weight: 700;")
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            c.video_frame.add_overlay(badge, "top-center")
            badge.hide()
            self._sim_badges.append(badge)
        self.camera_menu.openChanged.connect(self._on_menu_changed)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(SP[1], 0, SP[1], 0)
        sl.setSpacing(SP[1])
        sl.addWidget(self.follow_panel)
        sl.addWidget(self.controls)
        sl.addStretch(1)
        # агент — в немодальном окне из меню ☰ (модальное заблокировало бы API: 409)
        self.agent_panel = AgentPanel()  # разрешения агенту: по умолчанию выключены, не сохраняются
        self.agent_window = QDialog(self)
        self.agent_window.setWindowTitle("Агент")
        self.agent_window.setModal(False)
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
        central = QWidget()
        hl = QHBoxLayout(central)
        hl.addWidget(grid_host, 1)
        hl.addWidget(side_col)
        self.setCentralWidget(central)

        # строка состояния: слева сообщения; справа видео и анализ | главные клавиши | версия и подпись
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        keys = QWidget()
        kl = QHBoxLayout(keys)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(SP[0])
        for i, (key, what) in enumerate(_FOOTER_KEYS):
            if i:
                kl.addSpacing(SP[2])
            kl.addWidget(_keycap_label(key))
            kl.addWidget(self._caption(what))
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        self.credit_label = QLabel("by funpin.ru & Claude")
        self.credit_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        # отступ справа: в окне со скруглёнными углами подпись не упирается в край
        self.credit_label.setContentsMargins(0, 0, SP[2], 0)
        for wd in (self.video_pill, self.analysis_pill, _vline(), keys, _vline(), self.version_label,
                   self.credit_label):
            sb.addPermanentWidget(wd)
        # чёрный ящик: «Показать в папке» после F9 — слева в строке состояния
        self.show_folder_btn = QPushButton("Показать в папке")
        self.show_folder_btn.setProperty("variant", "ghost")
        self.show_folder_btn.hide()
        sb.addWidget(self.show_folder_btn)

        # одно меню ☰ в окне — одинаково на Mac и Windows; меню «Сервис» в строке macOS нет
        menu = self.main_menu_btn.menu()
        self.calib_action = menu.addAction("Калибровка камеры…", self._run_calibration)
        self.diag_action = menu.addAction("Диагностика камеры…", self._run_diagnostics)
        self.atem_action = menu.addAction("ATEM…", self._edit_atem)
        self.agent_action = menu.addAction("Агент…", self._show_agent_window)
        self.pads_action = menu.addAction("Геймпады…", self.show_gamepads)
        self.power_action = menu.addAction("Перевести камеру в режим ожидания…", self._power_menu)
        self.sim_action = menu.addAction("Симулятор (для тренировки)", self._open_sim_camera)
        menu.addSeparator()
        self.save_moment_action = QAction("Сохранить момент (F9)", self)  # не ручное управление: слежение не трогаем
        self.save_moment_action.triggered.connect(lambda: self.save_moment())
        self.open_replay_action = QAction("Открыть журнал…", self)
        self.open_replay_action.triggered.connect(self._choose_replay)
        self.rec_settings_action = QAction("Настройки журнала…", self)
        self.rec_settings_action.triggered.connect(self._recording_settings)
        box_menu = QMenu(self)
        for act in (self.save_moment_action, self.open_replay_action, self.rec_settings_action):
            box_menu.addAction(act)
        self.blackbox_btn.setMenu(box_menu)
        box_sub = menu.addMenu("Чёрный ящик")
        for act in (self.save_moment_action, self.open_replay_action, self.rec_settings_action):
            box_sub.addAction(act)
        menu.addSeparator()
        self.check_updates_action = QAction("Проверять обновления", self)
        self.check_updates_action.setCheckable(True)
        self.check_updates_action.setChecked(self.cfg.check_updates)
        menu.addAction(self.check_updates_action)
        self.check_updates_action.toggled.connect(self._on_check_updates_toggled)
        menu.addSeparator()
        analyze_menu = menu.addMenu("Анализировать камер одновременно")
        self.max_analyzed_group = QActionGroup(self)
        self.max_analyzed_group.setExclusive(True)
        self.max_analyzed_actions: list[QAction] = []
        for n in range(1, GRID_SLOTS + 1):
            act = analyze_menu.addAction(str(n))
            act.setCheckable(True)
            act.setData(n)
            act.setChecked(n == self.cfg.max_analyzed)
            self.max_analyzed_group.addAction(act)
            self.max_analyzed_actions.append(act)
        self.max_analyzed_group.triggered.connect(lambda act: self._on_max_analyzed(act.data()))
        self.update_action = menu.addAction("", self._open_release_page)
        self.update_action.setVisible(False)
        self._saveFinished.connect(self._on_save_finished)
        self._saved_path: Path | None = None
        self._hide_folder_timer = QTimer(self)
        self._hide_folder_timer.setSingleShot(True)
        self._hide_folder_timer.timeout.connect(self.show_folder_btn.hide)
        self.show_folder_btn.clicked.connect(self._show_saved_folder)

        self.follow_panel.followRequested.connect(self._on_follow_requested)
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
        # API агента: оператор сам включил/выключил слежение (пробел, кнопка) — слежение теперь его
        self.on_operator_follow: Callable[[bool], None] | None = None

        self._speed_scale_saved_t = float("-inf")
        self._recalib_hinted: set[str] = set()  # камеры, которым подсказка «откалибруйте заново» уже показана в этот запуск
        self._recalib_hint_for: str | None = None  # камера, у которой подсказка видна в нынешнем подключении
        self._unsupported_shown: tuple = ()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)

        QApplication.instance().installEventFilter(self)
        self._restart_atem()
        self._initial_grid()
        self._api = start_window_api(self)
        QTimer.singleShot(3000, self._start_update_check)

    # --- выбранная клетка ----------------------------------------------

    @property
    def selected_cell(self) -> Cell | None:
        sel = self.grid.selected
        return self.grid.cells[sel] if sel is not None else None

    @property
    def _cell(self) -> CameraCell:
        return self.cells[self.grid.selected or 0]

    @property
    def session(self) -> CameraSession | None:
        c = self.selected_cell
        return c.session if c else None

    @property
    def preview(self):
        return self._cell.preview

    @property
    def video_frame(self) -> VideoFrame:
        return self._cell.video_frame

    @property
    def power_overlay(self):
        return self._cell.power_overlay

    @property
    def target_label(self) -> QLabel:
        return self._cell.target_label

    @property
    def reset_target_btn(self) -> StatusPill:
        return self._cell.reset_target_btn

    @property
    def skeleton_check(self) -> StatusPill:
        return self._cell.skeleton_check

    def _make_session(self, cfg: CameraConfig, analysis: bool) -> CameraSession:
        return CameraSession(cfg, scene_path=self.cfg.sim_scene_path, app_cfg=self.cfg, analysis=analysis)

    # --- список камер и клетки ------------------------------------------

    def _cameras(self) -> list[CameraConfig]:
        # --sim добавляет временные камеры; если такая уже в настройках с тем же id, второй не нужно
        stored = {c.id for c in self.cfg.cameras}
        return self.cfg.cameras + [c for c in self._sim_cams if c.id not in stored]

    def _is_stored(self, cam: CameraConfig | None) -> bool:
        """Камера записана в настройки (её можно изменить и удалить); временный --sim — нет."""
        return cam is not None and any(c.id == cam.id for c in self.cfg.cameras)

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

    @staticmethod
    def _choice_label(c: CameraConfig) -> str:
        if c.kind == "sim":  # «Симулятор» в названии и так всё говорит: «(симулятор)» не дублируем
            return c.name if "симулятор" in c.name.lower() else f"{c.name} (симулятор)"
        return f"{c.name} · {c.ip}"

    @property
    def sim_badge(self) -> QLabel:
        """Метка «СИМУЛЯТОР» выбранной клетки (совместимость: окно на одну камеру)."""
        return self._sim_badges[self.grid.selected if self.grid.selected is not None else 0]

    def _sync_sim_badges(self) -> None:
        for c, badge in zip(self.grid.cells, self._sim_badges):
            badge.setVisible(c is not None and c.cfg.kind == "sim")

    def _current_cfg(self) -> CameraConfig | None:
        c = self.selected_cell
        return c.cfg if c else None

    def _states(self) -> list[CellState | None]:
        return [
            CellState(c.cfg.name, c.cfg.tracking, bool(c.session.follower and c.session.follower.enabled))
            if c else None
            for c in self.grid.cells
        ]

    def _refresh_cell_choices(self) -> None:
        """В списке клетки — камеры, не стоящие в других клетках (одна камера — одна клетка)."""
        for i, cell in enumerate(self.cells):
            items = [
                (c.id, self._choice_label(c))
                for c in self._cameras() if self.grid.slot_of(c.id) in (None, i)
            ]
            cur = self.grid.cells[i]
            cell.set_choices(items, cur.cfg.id if cur else None)

    def _initial_grid(self) -> None:
        by_id = {c.id: c for c in self._cameras()}
        ids = ["sim1", "sim2", None, None] if self.use_sim else list(self.cfg.grid)
        if not self.use_sim:  # на эфир не стартуем с симулятором: он открывается только из меню ☰
            ids = [i if i in by_id and by_id[i].kind != "sim" else None for i in ids]
            real = [c for c in self.cfg.cameras if c.kind != "sim"]
            if not any(ids) and real:
                ids[0] = real[0].id  # как раньше: без выбора открывается первая настоящая камера
        ids = [i if i in by_id else None for i in ids]
        occupied = [i for i, cid in enumerate(ids) if cid]
        want = self.cfg.last_camera_id if not self.use_sim else None
        sel = next((i for i, cid in enumerate(ids) if cid and cid == want), occupied[0] if occupied else 0)
        self.grid.selected = sel
        states = [CellState(by_id[c].name, by_id[c].tracking, False) if c else None for c in ids]
        alloc = allocate_analysis(states, sel, self.cfg.max_analyzed)
        for slot, cid in enumerate(ids):
            if cid:
                self._open_cell(slot, by_id[cid], analysis=slot in alloc)
        self._loading = False
        self._pad_sync_slots()
        self._reallocate_analysis()
        self._select_effects()
        self._refresh_cell_choices()
        self._update_selected_status()

    def select_slot(self, slot: int) -> None:
        """Выбрать клетку: клавиатура, мышь, геймпад и правая панель теперь относятся к ней."""
        if not 0 <= slot < GRID_SLOTS or slot == self.grid.selected:
            return
        self.camera_menu.close_menu()  # до переноса: ручной фокус получает ноль
        self.release_all_held()  # геймпад ведёт свою камеру и выбором клетки не трогается
        old = self.selected_cell
        if old is not None:
            old.mixer.release("keyboard")
            old.mixer.release("mouse")
        self.grid.selected = slot
        self._select_effects()
        self._reallocate_analysis()
        self._save_grid()
        self._update_selected_status()

    def _select_effects(self) -> None:
        """Всё, что зависит от выбранной клетки: рамка, правая панель, управление, оверлеи, огоньки геймпада."""
        sel = self.grid.selected
        for i, cell in enumerate(self.cells):
            cell.set_selected(i == sel)
        self._move_overlays(self._cell.video_frame)
        cam, cell = self._current_cfg(), self.selected_cell
        self._recalib_hint_for = None  # подсказка не возвращается после переключения туда-обратно
        # то, что про камеру уже известно, сразу: скрытые строки не мигают при каждом переключении
        known = tuple(sorted(f for f in cam.unsupported if isinstance(f, str))) if cam is not None else ()
        self._unsupported_shown = known
        self.controls.set_unsupported(known)
        real = cam is not None and cam.kind != "sim"
        self.edit_action.setEnabled(self._is_stored(cam))
        self.del_action.setEnabled(self._is_stored(cam))
        self._sync_sim_badges()
        want = cam.stream if cam else "auto"
        for act in self.stream_actions.actions():
            act.setChecked(act.data() == want)
            act.setEnabled(real)
        self.controls.bind(self._source(cell, "mouse") if cell else None)
        if cam is not None:
            self.follow_panel.set_composition(cam.composition)
            if cam.composition.startswith("waist"):
                self._last_waist = cam.composition
            self.follow_panel.set_effort(cam.effort)
            self.follow_panel.set_calibrated(cam.calibration is not None)
        self.follow_panel.set_state(False, None)
        self.follow_panel.status_label.hide()
        self._view_shown = None
        self._tally_shown = None
        self._sync_pad_player()

    def _move_overlays(self, frame: VideoFrame) -> None:
        if self._overlay_host is frame:
            return
        w = self.follow_panel.status_label
        hidden = w.isHidden()
        if self._overlay_host is not None:
            self._overlay_host.remove_overlay(w)
        frame.add_overlay(w, "bottom-left")
        w.setVisible(not hidden)
        self._overlay_host = frame
        if self._menu_pad is None:
            self._menu_to(frame)

    def _menu_to(self, frame: VideoFrame) -> None:
        if self._menu_host is frame:
            return
        m = self.camera_menu
        hidden = m.isHidden()
        if self._menu_host is not None:
            self._menu_host.remove_overlay(m)
        frame.add_overlay(m, "center")
        m.setVisible(not hidden)
        self._menu_host = frame

    def _menu_slot(self) -> int | None:
        """Клетка, на которую действует меню Options: камера геймпада, если меню открыли с него."""
        if self._menu_pad is not None:
            return self.pad_router.slot_of(self._menu_pad)
        return self.grid.selected

    def menu_session(self) -> CameraSession | None:
        """Сессия, на которую действует меню Options (читает API: агент не трогает эту камеру, пока меню открыто)."""
        return self._menu_session()

    def _menu_session(self) -> CameraSession | None:
        slot = self._menu_slot()
        c = self.grid.cells[slot] if slot is not None else None
        return c.session if c else None

    def _source(self, cell: Cell, name: str) -> MixerSource:
        """Источник смесителя клетки (один объект на клетку и имя: он помнит свои оси)."""
        slot = self.grid.cells.index(cell)
        key = (slot, name)
        got = self._srcs.get(key)
        if got is None or got[0] is not cell:
            got = (cell, cell.mixer.source(name))
            self._srcs[key] = got
        return got[1]

    def _on_cell_chosen(self, slot: int, camera_id) -> None:
        """Оператор выбрал камеру в списке клетки."""
        if slot != self.grid.selected:
            self.select_slot(slot)
        try:
            self.set_cell_camera(slot, camera_id)
        except ValueError as e:
            log.warning("%s", e)
            self._refresh_cell_choices()

    def set_cell_camera(self, slot: int, camera_id: str | None) -> None:
        """Открыть камеру в клетке (или закрыть клетку, если `camera_id` — None).

        Камера, уже стоящая в другой клетке, — ValueError, клетка остаётся как была."""
        if not 0 <= slot < GRID_SLOTS:
            raise ValueError(f"нет клетки {slot}")
        cam = None
        if camera_id is not None:
            cam = next((c for c in self._cameras() if c.id == camera_id), None)
            if cam is None:
                raise ValueError(f"нет камеры {camera_id}")
            other = self.grid.slot_of(camera_id)
            if other is not None and other != slot:
                raise ValueError(f"камера {camera_id} уже открыта в клетке {other + 1}")
        self._open_cell(slot, cam)

    def _open_cell(self, slot: int, cam: CameraConfig | None, analysis: bool | None = None) -> None:
        other = self.grid.slot_of(cam.id) if cam is not None else None
        if other is not None and other != slot:  # до разборки клетки: отказ оставляет её как была
            raise ValueError(f"камера {cam.id} уже открыта в клетке {other + 1}")
        selected = slot == self.grid.selected
        if self.camera_menu.is_open and self._menu_slot() == slot:
            self.camera_menu.close_menu()  # до остановки сессии: ручной фокус получает ноль
        if selected:
            self.release_all_held()
        d = self._cell_dialogs.pop(slot, None)
        if d is not None:
            d.reject()  # диалог относился к закрываемой камере
        self._speed_scale_saved.pop(slot, None)  # «раз в минуту» — для каждой камеры заново
        self._srcs = {k: v for k, v in self._srcs.items() if k[0] != slot}
        cell = self.cells[slot]
        cell.preview.set_source(None)
        cell.preview.set_analyzer(None)
        cell.preview.set_follow(None)
        self._tag_shown.pop(slot, None)
        self._view_shown = None if selected else self._view_shown
        if cam is None:
            self.grid.close(slot)
            cell.video_frame.tracking_tag.hide()
            cell.video_frame.set_tally(None)
        else:
            if analysis is None:
                states = self._states()
                states[slot] = CellState(cam.name, cam.tracking, False)
                analysis = slot in allocate_analysis(states, self.grid.selected, self.cfg.max_analyzed)
            try:
                opened = self.grid.open(slot, cam, analysis)
            except ValueError:
                raise
            except Exception as e:
                log.exception("Не удалось открыть камеру %s", cam.name)
                self.statusBar().showMessage(f"Не удалось открыть камеру {cam.name}: {e}", 8000)
                opened = None
            if opened is not None:
                s = opened.session
                opened.mixer.pt_scale = lambda s=s: self._manual_pt_scale(s)
                for p in self.pad_router.pads_on(slot):  # камеру в клетке сменили: новая поедет после центра стика
                    self._pad_hold_on_new(p, opened)
                cell.preview.set_source(s.frames)
                cell.preview.camera_name = cam.name
                if s.follower:
                    s.follower.on_change = self._follow_cb(s)  # поток слежения
        self._pad_sync_slots()  # закрытая клетка переводит её геймпады по правилу Create
        self.agent_panel.refresh()  # строки панели «Агент»; смена камеры сняла флажки прежней (её шлюз закрыт)
        if self._loading:
            return
        self._tally_shown = None
        if selected:
            self._select_effects()
        self._reallocate_analysis()
        self._sync_sim_badges()
        self._refresh_cell_choices()
        self._refresh_service_cells()
        self._save_grid()
        self._update_selected_status()

    def _save_grid(self) -> None:
        """Сетка и последняя выбранная камера — в настройки (в --sim ничего не пишем)."""
        if self.use_sim or self._loading:
            return
        self.cfg.grid = self.grid.ids()
        c = self.selected_cell
        if c is not None:
            self.cfg.last_camera_id = c.cfg.id
        self._save()

    def _reallocate_analysis(self) -> None:
        """Нейросеть — слежущим, затем выбранной и остальным по порядку, пока не кончился лимит."""
        if self._loading:
            return
        alloc = allocate_analysis(self._states(), self.grid.selected, self.cfg.max_analyzed)
        for i, c in enumerate(self.grid.cells):
            if c is not None:
                c.session.set_analysis(i in alloc)
        for i, c in enumerate(self.grid.cells):  # анализатор и слежение могли смениться — перепривязать
            if c is None:
                continue
            self.cells[i].preview.set_analyzer(c.session.analyzer)
            self.cells[i].preview.set_follow(c.session.follower)
            if c.session.follower:
                c.session.follower.on_change = self._follow_cb(c.session)
        self._pad_limit_binding()

    def _pad_limit_binding(self) -> None:
        for i, c in enumerate(self.grid.cells):
            lw = c.session.limit_watch if c else None
            if lw is not None:
                bs = [self.pads[p] for p in self.pad_router.pads_on(i) if p in self.pads]
                lw.set_on_limit((lambda bs=bs: [b.notify_limit() for b in bs]) if bs else (lambda: None))

    def _on_max_analyzed(self, n: int) -> None:
        self.cfg.max_analyzed = int(n)
        self._save()
        self._reallocate_analysis()
        self._update_selected_status()

    # --- слежение -----------------------------------------------------

    def _follow_cb(self, session: CameraSession) -> Callable[[bool, str | None], None]:
        def on_change(on: bool, reason: str | None) -> None:
            """Follower.on_change (из любого потока): раскачка/аварийный стоп снимают агента; вибрация геймпада."""
            try:
                if revokes_agent(on, reason):  # раскачка или аварийный стоп — разрешения этой камеры
                    cell = next((c for c in self.grid.cells if c is not None and c.session is session), None)
                    if cell is not None:
                        cell.gate.revoke()
            finally:
                for n, b in list(self.pads.items()):
                    if session is self._pad_session(n):
                        b.notify_follow(on, reason)
        return on_change

    def _follower(self):
        return self.session.follower if self.session else None

    def _on_follow_requested(self, want: bool, by_agent: bool = False) -> None:
        if not by_agent:
            if not want:  # оператор забрал камеру — как Esc: агент теряет разрешение до следующей команды
                self.agent_panel.revoke()
            if self.on_operator_follow:
                self.on_operator_follow(want)
        if want and self.selected_cell is not None:
            refusal = tracking_refusal(self._states(), self.grid.selected, self.cfg.max_analyzed)
            if refusal is not None:
                self.follow_panel.set_state(False, None)
                self.follow_panel.show_hint(refusal)
                self._view_shown = self._current_view()  # подсказка остаётся, пока вид не изменится сам
                return
        f = self._follower()
        if f is None:
            self.follow_panel.set_state(False, "распознавание недоступно")
            self._view_shown = self._current_view()  # сообщение остаётся, пока вид не изменится сам
            return
        if want:
            if not f.enable():
                v = self._current_view()
                if v.kind == "swing":
                    self.follow_panel.set_view(v)  # раскачка важнее подсказки: плитка и «Калибровка…» остаются
                else:
                    self.follow_panel.set_state(False, None)
                    self.follow_panel.show_hint("Сначала кликните по человеку на видео.")
                # подсказка остаётся, пока вид не изменится сам
                self._view_shown = v
        else:
            f.disable("выключено оператором")
        self._sync_follow()

    # --- для API агента (вызывать в потоке Qt) ---------------------------

    def request_follow(self, on: bool, slot: int | None = None) -> bool:
        """Включить/выключить слежение тем же путём, что пробел. Возвращает, включено ли оно теперь.

        `slot` — клетка не выбранной камеры: там панель не меняется, проверки лимита те же."""
        if slot is None or slot == self.grid.selected:
            self._on_follow_requested(on, by_agent=True)
            f = self._follower()
            return bool(f and f.enabled)
        c = self.grid.cells[slot]
        f = c.session.follower if c else None
        if f is None:
            return False
        if not on:
            f.disable("выключено агентом")
        elif tracking_refusal(self._states(), slot, self.cfg.max_analyzed) is None:
            f.enable()
        self._sync_follow()
        return f.enabled

    def apply_composition(self, key: str, slot: int | None = None) -> None:
        if slot is None or slot == self.grid.selected:
            self._on_composition(key)
            return
        c = self.grid.cells[slot]
        if c is None:
            return
        c.cfg.composition = key
        if c.cfg.kind != "sim":
            self._save()
        if c.session.follower:
            c.session.follower.set_composition(COMPOSITIONS[key])

    def apply_effort(self, x: float, slot: int | None = None) -> None:
        if slot is None or slot == self.grid.selected:
            self.follow_panel.set_effort(x)
            self._on_effort(x)
            return
        c = self.grid.cells[slot]
        if c is None:
            return
        c.cfg.effort = x
        if c.cfg.kind != "sim":
            self._save()
        if c.session.follower:
            c.session.follower.set_effort(EffortProfile.at(x))

    def refresh_status(self) -> None:
        self._update_status()

    # --- новая версия -------------------------------------------------

    def _start_update_check(self) -> None:
        """Одна проверка в фоне; не чаще раза в сутки и только если не отключена в меню."""
        if self._checker is not None or self._checker_factory is None or not self.cfg.check_updates:
            return
        if is_newer(self.cfg.latest_version, __version__):  # найдено прошлой проверкой — показать сразу, без сети
            self._show_update(UpdateInfo(self.cfg.latest_version, RELEASE_PAGE.format(version=self.cfg.latest_version)))
        if time.time() - self.cfg.last_update_check < CHECK_INTERVAL_S:
            return
        self._checker = self._checker_factory(__version__)
        self._checker.start()

    def _poll_update_check(self) -> None:
        """Из таймера состояния: читаем снимок, сеть не ждём."""
        c = self._checker
        if c is None or not c.done:
            return
        self._checker = None
        info = c.result
        if c.ok:
            self.cfg.last_update_check = time.time()
            self.cfg.latest_version = info.version if info is not None else ""
            self._save()
        if info is not None:
            self._show_update(info)
        elif c.ok:
            self.update_action.setVisible(False)
            self.main_menu_btn.set("", "off", "Меню")

    def _show_update(self, info: UpdateInfo) -> None:
        self._update_info = info
        self.update_action.setText(f"Доступна версия {info.version} — открыть страницу")
        self.update_action.setVisible(True)
        self.main_menu_btn.set("", "accent", f"Меню · доступна версия {info.version}")

    def _open_release_page(self) -> None:
        if self._update_info is not None:
            QDesktopServices.openUrl(QUrl(self._update_info.html_url))

    def _on_check_updates_toggled(self, on: bool) -> None:
        self.cfg.check_updates = on
        self._save()

    def save_config(self) -> None:
        self._save()

    def toggle_follow(self) -> None:
        f = self._follower()
        self._on_follow_requested(not (f and f.enabled))

    def _current_view(self, cell: Cell | None = None) -> TrackingView:
        cell = cell if cell is not None else self.selected_cell
        s = cell.session if cell else None
        f = s.follower if s else None
        a = s.analyzer if s else None
        r = a.latest() if a else None
        return tracking_view(
            bool(f and f.enabled),
            f.last_reason if f else None,
            r.target_id if r else None,
            r.target_state if r else None,
            diverged=bool(f and getattr(f.controller, "diverged", False)),
        )

    def _sync_follow(self) -> None:
        """Состояние слежения меняется и из других потоков (ручное управление, сбой анализа) — показать."""
        ts = frozenset(
            i for i, c in enumerate(self.grid.cells) if c and c.session.follower and c.session.follower.enabled
        )
        if ts != self._tracking_set:
            self._tracking_set = ts
            self._reallocate_analysis()  # слежение кончилось — место анализа свободно
        for i, c in enumerate(self.grid.cells):
            v = self._current_view(c) if c else None
            if v != self._tag_shown.get(i):
                self._tag_shown[i] = v
                if v is not None:
                    self.cells[i].video_frame.set_tracking_tag(v.tag, v.color)
        v = self._current_view()
        if v != self._view_shown:
            self._view_shown = v
            self.follow_panel.set_view(v)

    def _restart_atem(self) -> None:
        """Остановить старый клиент ATEM и создать новый из cfg.atem_ip (пусто — Tally выключен)."""
        if self.atem:
            self.atem.stop()
            self.atem = None
        if self.cfg.atem_ip:
            self.atem = AtemTally(self.cfg.atem_ip, port=self._atem_port)
            self.atem.start()
        self._tally_shown = None
        self._sync_tally()

    def _edit_atem(self) -> None:
        d = AtemDialog(self.cfg.atem_ip, self)
        if not d.exec():
            return
        self.cfg.atem_ip = d.ip()
        self._save()
        self._restart_atem()

    def _sync_tally(self) -> None:
        """Tally из снимка клиента (сеть не ждём): рамка каждой клетки и чип выбранной, обновляются только при изменении."""
        configured = self.atem is not None
        snap = self.atem.snapshot() if self.atem else None
        online = bool(snap and snap.online)
        per = []
        for c in self.grid.cells:
            inp = atem_input_of(c.cfg) if c else 0
            per.append((inp, snap.state(inp) if snap and inp else None, bool(snap and inp and snap.known(inp))))
        key = (configured, online, tuple(per), self.grid.selected)
        if key == self._tally_shown:
            return
        self._tally_shown = key
        for cell, (_inp, state, _known) in zip(self.cells, per):
            cell.video_frame.set_tally(state if online else None)
        inp, state, known = per[self.grid.selected or 0]
        pill = atem_pill(configured, online, inp, state, known=known)
        if pill is None:
            self.atem_pill.hide()
        else:
            self.atem_pill.set(*pill)
            self.atem_pill.show()
        self._fit_top_bar()

    @staticmethod
    def _caption(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        return lab

    def _on_composition(self, key: str) -> None:
        if key.startswith("waist"):
            self._last_waist = key
        cam = self._current_cfg()
        self.follow_panel.set_composition(key)
        if cam is None:
            return
        cam.composition = key
        if self._is_stored(cam):
            self._save()
        if f := self._follower():
            f.set_composition(COMPOSITIONS[key])

    def _on_effort(self, x: float) -> None:
        cam = self._current_cfg()
        if cam is None:
            return
        cam.effort = x
        if self._is_stored(cam):
            self._save()
        if f := self._follower():
            f.set_effort(EffortProfile.at(x))

    def _reset_target(self, slot: int | None = None) -> None:
        c = self.grid.cells[self.grid.selected if slot is None else slot]
        if c and c.session.analyzer:
            c.session.analyzer.clear_target()
        self._update_status()

    def _on_skeleton(self, on: bool, cell: CameraCell | None = None) -> None:
        cell = cell or self._cell
        cell.preview.show_skeleton = on
        cell.preview.update()

    def _on_stream_changed(self, key: str) -> None:
        cam = self._current_cfg()
        if not cam or cam.kind == "sim":
            return
        cam.stream = key
        self._save()
        self._open_cell(self.grid.selected, cam)

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
        dlg = DiagnosticsDialog(cam.ip, cam.username, password, parent=self)
        dlg.setWindowTitle(f"{dlg.windowTitle()} — {cam.name}")
        dlg.exec()
        if self.grid.cells[slot] is None and self.grid.slot_of(cam.id) is None:
            self._open_cell(slot, cam)

    # --- запись (чёрный ящик) ------------------------------------------

    def save_moment(self, reason: str = "F9", slot: int | None = None):
        """F9: сохранить момент у каждой открытой камеры. Возвращает запись клетки `slot` (по умолчанию —
        выбранной или первой открытой)."""
        cells = [c for c in self.grid.cells if c is not None]
        if not cells:
            self.statusBar().showMessage("Нечего сохранять: камера не выбрана.", 5000)
            return None
        self.statusBar().showMessage("Сохраняю запись…")
        batch = {"left": len(cells), "paths": [], "errors": []}
        chosen = None
        want = self.grid.cells[slot] if slot is not None else self.selected_cell
        for c in cells:
            fut = c.session.save_moment(reason)
            if chosen is None or c is want:
                chosen = fut
            fut.add_done_callback(lambda f, b=batch: self._saveFinished.emit((b, f)))  # рабочий поток → сигнал
        return chosen

    def _on_save_finished(self, item) -> None:
        batch, fut = item
        batch["left"] -= 1
        try:
            batch["paths"].append(Path(fut.result()))
        except Exception as e:
            log.warning("Запись не сохранена: %s", e)
            batch["errors"].append(e)
        if batch["left"] > 0:
            return
        paths, errors = batch["paths"], batch["errors"]
        if len(paths) + len(errors) == 1:
            if errors:
                self.statusBar().showMessage(f"Запись не сохранена: {errors[0]}", 10000)
            else:
                self.statusBar().showMessage(f"Запись сохранена: {paths[0].name}", 10000)
        elif paths:
            bad = f", не удалось: {len(errors)}" if errors else ""
            self.statusBar().showMessage(f"Записей сохранено: {len(paths)}{bad}", 10000)
        else:
            self.statusBar().showMessage(f"Записи не сохранены: {errors[0]}", 10000)
        if paths:  # после showMessage: оно прячет временные виджеты строки состояния
            self._saved_path = paths[-1]
            self.show_folder_btn.show()
            self._hide_folder_timer.start(10000)

    def _show_saved_folder(self) -> None:
        if self._saved_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._saved_path.parent)))

    def _choose_replay(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ptztracker.config import record_dir

        path, _ = QFileDialog.getOpenFileName(self, "Открыть запись", str(record_dir(self.cfg)), "Записи (*.zip)")
        if path:
            self.open_replay(Path(path))

    def open_replay(self, path: Path) -> QWidget | None:
        """Окно просмотра записи; камера и слежение не затрагиваются."""
        from ptztracker.recorder.reader import Episode
        from ptztracker.ui.replay_window import ReplayWindow

        try:
            # отдельное окно: клавиши в нём не управляют камерой (фильтр главного окна его не считает своим)
            w = ReplayWindow(Episode.open(path), parent=self, on_escape=self.emergency_stop)
        except Exception as e:
            QMessageBox.warning(self, "Запись", f"Не удалось открыть запись {path.name}: {e}")
            return None
        w.setAttribute(Qt.WA_DeleteOnClose)
        w.show()
        return w

    def _recording_settings(self) -> None:
        from ptztracker.ui.recording_dialog import RecordingDialog

        d = RecordingDialog(self.cfg, parent=self)
        if d.exec():
            d.apply()
            self._save()

    # --- статус -------------------------------------------------------

    def service_kind(self, slot: int) -> str | None:
        """Какая служебная клетка стоит в пустой клетке `slot` (первая пустая — position, вторая — graphs, третья — summary)."""
        empty = [i for i, c in enumerate(self.grid.cells) if c is None]
        return KINDS[empty.index(slot)] if slot in empty and empty.index(slot) < len(KINDS) else None

    def _service_pads(self, slot: int) -> list[int]:
        return self.pad_router.pads_on(slot)

    def _refresh_service_cells(self) -> None:
        empty = [i for i, c in enumerate(self.grid.cells) if c is None]
        want = {k: (empty[n] if n < len(empty) else None) for n, k in enumerate(KINDS)}
        moved = [k for k in self._service if self._service_slot[k] != want[k]]
        for kind in moved:  # два прохода: снять все переезжающие, потом поставить — иначе соседний снимет новый
            if self._service_slot[kind] is not None:
                self.cells[self._service_slot[kind]].set_service(None)
        for kind in moved:
            if want[kind] is not None:
                self.cells[want[kind]].set_service(self._service[kind])
            self._service_slot[kind] = want[kind]
        if not any(w is not None for w in want.values()):
            return
        rows = []
        for i, c in enumerate(self.grid.cells):
            if c is None:
                continue
            s = c.session
            r = s.analyzer.latest() if s.analyzer else None
            rows.append(ServiceRow(i, c.cfg.name, s.ptz, s.ptz_history(), r.fps if r else None,
                                   s.online, self._service_pads(i)))
        for kind, widget in self._service.items():
            if want[kind] is not None:
                widget.update_view(rows, self.grid.selected)

    def _poll_positions(self) -> None:
        """Положение камер нужно только видимым клеткам position/graphs; poll_position лишь ставит запрос в очередь."""
        if self._service_slot["position"] is None and self._service_slot["graphs"] is None:
            return
        for c in self.grid.cells:
            if c is not None:
                c.session.poll_position()

    def _update_status(self) -> None:
        self._poll_update_check()
        self._refresh_manual_speed()
        self._refresh_service_cells()
        self._poll_positions()
        for i, c in enumerate(self.grid.cells):
            cell = self.cells[i]
            if c is None:
                cell.power_overlay.set_view("on", "")
                self._update_target_label(cell, None)
                continue
            s = c.session
            cell.power_overlay.set_view(power_view(s.power, s.online), s.cfg.name, s.supports("power"))
            self._remember_rtsp_url(s)
            self._remember_speed_scale(s, i)
            self._remember_camera_info(s)
            self._update_target_label(cell, s)
        self._update_selected_status()

    def _update_selected_status(self) -> None:
        """Значки верхней строки и правая панель — для выбранной клетки."""
        s = self.session
        cam = self._current_cfg()
        can_power = s is not None and s.supports("power")  # ER1 на питание: кнопка остаётся, но серая
        self.power_action.setText("Включить камеру" if s and s.power == "standby"
                                  else "Перевести камеру в режим ожидания…")
        self.power_action.setEnabled(can_power)
        self.power_action.setToolTip("" if can_power or s is None else "Камера не принимает команду питания от программы")
        if not s:
            self.video_pill.set(*video_pill(None, ""))
            self.analysis_pill.set("", "off", "Анализ: камера не выбрана")
            self.blackbox_btn.set("", "off", "Чёрный ящик: камера не выбрана")
            self._fit_top_bar()
            return
        self._update_recalibration_hint(s, cam)
        stream = STREAM_CHOICES.get(cam.stream if cam else "auto", "Авто")
        st = s.frames.stats()
        text, kind, tip = video_pill(st, stream)
        if not s.online:
            kind, tip = "fault", f"Нет связи с камерой {s.cfg.name} ({s.cfg.ip})\n{tip}"
        self.video_pill.set(text, kind, tip)
        mins = s.app_cfg.record_buffer_s // 60
        self.blackbox_btn.set(
            "", "neutral" if s.recorder.active else "off",
            f"Чёрный ящик: хранит логи последних {mins} мин (не видео). F9 — сохранить момент"
            if s.recorder.active else "Чёрный ящик выключен",
        )
        self._update_analysis_status(s)
        self.controls.sync_image(s.image)
        unsupported = s.camera_info().unsupported
        if unsupported != self._unsupported_shown:
            self._unsupported_shown = unsupported
            self.controls.set_unsupported(unsupported)
        self._fit_top_bar()

    def _update_analysis_status(self, s: CameraSession) -> None:
        a = s.analyzer
        r = a.latest() if a else None
        if a is None:
            why = ("Без анализа: слежение на этой камере выключено в настройках" if not s.cfg.tracking
                   else "Без анализа: место анализа занято другой камерой")
            self.analysis_pill.set("", "off", why)
        else:
            self.analysis_pill.set(*analysis_pill(a.error, r.fps if r else None, r.infer_ms if r else None,
                                                  r.device if r else None))

    @staticmethod
    def _update_target_label(cell: CameraCell, s: CameraSession | None) -> None:
        a = s.analyzer if s else None
        r = a.latest() if a else None
        if r is None or r.target_id is None:
            cell.target_label.setText("Цель: не выбрана — кликните по человеку на видео")
            cell.target_label.setStyleSheet("")
        elif r.target_state == "tracking":
            cell.target_label.setText(f"Цель: №{r.target_id} — веду")
            cell.target_label.setStyleSheet(f"color: {C.ON}; font-weight: bold;")
        else:
            cell.target_label.setText(f"Цель: №{r.target_id} — потеряна")
            cell.target_label.setStyleSheet(f"color: {C.LOST}; font-weight: bold;")

    def _fit_top_bar(self) -> None:
        """Узкое окно: ATEM теряет текст; камера и TRACK — никогда."""
        if not hasattr(self, "_top"):
            return  # ещё строится окно
        pills = (self.atem_pill,)
        for p in pills:
            p.set_compact(False)
        avail = self.width() - 2 * SP[1]
        for p in pills:
            if self._top.sizeHint().width() <= avail:
                break
            p.set_compact(True)

    def _menu_button(self, icon_name: str, tooltip: str) -> StatusPill:
        b = StatusPill(icon_name, "", "neutral")
        b.set("", "neutral", tooltip)
        b.setPopupMode(QToolButton.InstantPopup)
        b.setMenu(QMenu(self))
        return b

    def _power_menu(self) -> None:
        s = self.session
        if s is None:
            return
        if s.power == "standby":
            self._power_on()
            return
        # как любое ручное действие: слежение и агент выключаются до вопроса и до #O0
        self.agent_panel.revoke()
        self.release_all_held()
        if f := self._follower():
            f.disable("выключено оператором")
        self._sync_follow()
        q = (f"Перевести камеру {s.cfg.name} в режим ожидания?\n"
             "Видео пропадёт; включение займёт около 8 секунд.")
        if QMessageBox.question(self, "Режим ожидания", q) == QMessageBox.Yes:
            s.set_power(False)

    def _power_on(self, slot: int | None = None) -> None:
        c = self.grid.cells[self.grid.selected if slot is None else slot]
        if c:
            c.session.set_power(True)
            self._update_status()

    def _show_agent_window(self) -> None:
        self.agent_window.show()
        self.agent_window.raise_()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit_top_bar()

    def _remember_rtsp_url(self, s: CameraSession) -> None:
        """Сохранить подобранный адрес видео (без пароля), чтобы в следующий раз открывался сразу."""
        found = s.found_rtsp_url
        if not found:
            return
        s.found_rtsp_url = None
        url = strip_credentials(found)
        cam = next((c for c in self.cfg.cameras if c.id == s.cfg.id), None)
        if cam and cam.rtsp_url != url:
            cam.rtsp_url = url
            self._save()
            self.statusBar().showMessage(f"Найден адрес видео: {url}", 8000)

    def _remember_camera_info(self, s: CameraSession) -> None:
        """Модель, прошивку и найденные «не умею» — в настройки камеры (сессия настройки не пишет).
        Известную модель не затираем пустым ответом; симулятору сведения не нужны."""
        cam = next((c for c in self.cfg.cameras if c.id == s.cfg.id), None)
        if cam is None or cam.kind == "sim":
            return
        info = s.camera_info()
        model = info.model or cam.model
        # прошивка: новая модель — прошивка из ответа (она могла быть None), та же — известную не теряем
        firmware = info.firmware or (cam.firmware if model == cam.model else None)
        unsupported = list(info.unsupported)
        if (model, firmware, set(unsupported)) == (cam.model, cam.firmware, set(cam.unsupported)):
            return
        cam.model, cam.firmware, cam.unsupported = model, firmware, unsupported
        self._save()
        log.info("Сведения о камере %s сохранены: %s %s, не умеет: %s", cam.name, model, firmware, unsupported)

    def _update_recalibration_hint(self, s: CameraSession, cam: CameraConfig | None) -> None:
        """Мягкая подсказка под кнопками кадра, если калибровка сделана на другой камере или в прежней версии.
        Не блокирует ничего; для каждой камеры показывается один раз за запуск (до смены камеры)."""
        hint = None
        info = s.camera_info()
        if cam is not None and cam.kind != "sim" and (info.model is not None or s.online):
            hint = recalibration_hint(cam.calibration, info.model, info.firmware)
        if hint and (cam.id == self._recalib_hint_for or cam.id not in self._recalib_hinted):
            self._recalib_hinted.add(cam.id)
            self._recalib_hint_for = cam.id
        else:
            hint = None
        self.follow_panel.set_calibration_note(hint)

    def _remember_speed_scale(self, s: CameraSession, slot: int) -> None:
        """Поправку скорости, найденную слежением, — в настройки камеры (не чаще раза в SPEED_SCALE_SAVE_S).
        Поток слежения настройки не пишет: только читает поправку отсюда, из потока Qt."""
        f = s.follower
        if f is None or f.polling_paused or time.monotonic() - self._speed_scale_saved.get(slot, float('-inf')) < SPEED_SCALE_SAVE_S:
            return  # идёт калибровка: её итог сбросит поправку
        cam = next((c for c in self.cfg.cameras if c.id == s.cfg.id), None)
        if cam is None or cam.kind == "sim":  # симулятору поправка скорости не нужна, в настройки её не пишем
            return
        scale: dict = {k: round(v, 3) for k, v in f.speed_scale.items()}
        old = cam.speed_scale if isinstance(cam.speed_scale, dict) else {}
        if cam.calibration is None:
            scale["table"] = DEFAULT_TABLE_ID  # поправка к таблице без калибровки — с её ярлыком
        if old.get("table") == scale.get("table") and all(
            abs(v - float(old.get(k, 1.0))) < SPEED_SCALE_SAVE_DIFF for k, v in scale.items() if k != "table"
        ):
            return
        cam.speed_scale = scale
        self._speed_scale_saved[slot] = time.monotonic()
        self._save()
        log.info("Поправка скорости камеры %s сохранена: %s", cam.name, scale)

    # --- горячие клавиши ---------------------------------------------

    def _is_mine(self, obj) -> bool:
        return obj is self or (isinstance(obj, QWidget) and self.isAncestorOf(obj))

    def eventFilter(self, obj, event):  # noqa: N802
        et = event.type()
        if et in (QEvent.KeyPress, QEvent.KeyRelease) and self._is_mine(obj):
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QAbstractSpinBox)):
                return False
            key = event.key()
            if key == Qt.Key_Space:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.toggle_follow()
                return True
            comp_keys = {Qt.Key_1 + i: k for i, k in enumerate(COMPOSITIONS)}
            if key in comp_keys:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self._on_composition(comp_keys[key])
                return True
            if key == Qt.Key_F1:
                if et == QEvent.KeyPress and not event.isAutoRepeat():
                    self.show_hotkeys()
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

    def emergency_stop(self) -> None:
        """Общий аварийный стоп (Esc и кнопка «Стоп»): снять разрешение агенту, отпустить клавиши,
        выключить слежение и остановить все камеры."""
        self.agent_panel.revoke_all()
        self._pad_stop_motion()  # стоп обнуляет и геймпад: снова поедет после возврата стика в центр
        self.release_all_held()
        self.grid.emergency_stop_all()
        self.camera_menu.close_menu()  # сначала стоп, потом закрытие

    def _menu_key(self, key: int, press: bool) -> None:
        m = self.camera_menu
        if key in (Qt.Key_Left, Qt.Key_Right):
            m.nav((key == Qt.Key_Right) - (key == Qt.Key_Left), 0) if press else m.nav(0, 0)
        elif press and key in (Qt.Key_Up, Qt.Key_Down):
            m.nav(0, (key == Qt.Key_Down) - (key == Qt.Key_Up))
        elif press:
            m.accept()

    def _menu_is_open(self) -> bool:
        """Читает поток опроса геймпада (мост создаётся раньше меню)."""
        m = getattr(self, "camera_menu", None)
        return bool(m and m.is_open)

    def _on_menu_changed(self, _open: bool) -> None:
        if not _open and self._menu_pad is not None:
            self._menu_pad = None
            sel = self.selected_cell
            if sel is not None:
                self._menu_to(self.cells[self.grid.selected].video_frame)
        for b in self._bridges:
            b.cancel_holds()
        self._sync_pad()

    def _apply_held(self) -> None:
        """Клавиатура — в смеситель выбранной клетки, геймпад — в смеситель его клетки; сумма источников ±1 по оси."""
        h = self._held
        pan = shape(KEY_PAN_SPEED, self.cfg.stick_sensitivity)  # стрелки — через ту же кривую, что мышь
        kv = (pan * ((Qt.Key_Right in h) - (Qt.Key_Left in h)),
              pan * ((Qt.Key_Up in h) - (Qt.Key_Down in h)),
              KEY_ZOOM_SPEED * (bool(h & _ZOOM_IN_KEYS) - bool(h & _ZOOM_OUT_KEYS)))
        kc = self.selected_cell
        if kc is not None:
            self._send_velocity(self._source(kc, "keyboard"), kv)
        for n, b in list(self.pads.items()):
            self.pad_velocity(n, *b.velocity())

    # --- чувствительность ручного управления ---------------------------

    def _manual_pt_scale(self, s: CameraSession) -> float:
        """Множитель ручного поворота камеры: «медленнее на крупном плане» по её зуму; слежение его не видит."""
        return tele_scale(s.zoom_x) if self.cfg.slow_on_tele else 1.0

    def _on_stick_sensitivity(self, sens: int) -> None:
        self.cfg.stick_sensitivity = clamp_sens(sens)
        self._save()
        if self._held:
            self._apply_held()

    def _on_slow_tele(self, on: bool) -> None:
        self.cfg.slow_on_tele = bool(on)
        self._save()
        self._refresh_manual_speed()
        if self.gamepads_dialog is not None:
            self.gamepads_dialog.set_slow_on_tele(on)

    def pad_rows(self) -> list[PadRow]:
        """Геймпады для окна «Геймпады…»: все, что программа видела (config), и подключённые сейчас."""
        live: dict[str, GamepadBridge] = {}
        for b in self._bridges:
            pid = (b.info or {}).get("id") if b.connected else None
            if pid:
                live.setdefault(pid, b)
        rows = []
        for pid in dict.fromkeys([*self.cfg.pad_names, *live]):
            b = live.get(pid)
            name = self.cfg.pad_names.get(pid) or ((b.info or {}).get("name") if b else None) or "Геймпад"
            cell = self._pad_slot_number(self._pad_nums.get(b)) if b is not None else None
            rows.append(PadRow(pid, name, self.pad_sensitivity({"id": pid}), b is not None, cell,
                               self.pad_l1r1_mode(pid), self.pad_speed_gear(pid)))
        return rows

    def show_gamepads(self) -> None:
        if self.gamepads_dialog is None:
            self.gamepads_dialog = GamepadsDialog(
                self.pad_rows(), self.cfg.slow_on_tele, self.set_pad_sensitivity,
                self.controls.slow_tele_check.setChecked, parent=self, on_mode=self.set_pad_l1r1_mode)
        else:
            self.gamepads_dialog.set_rows(self.pad_rows())
            self.gamepads_dialog.set_slow_on_tele(self.cfg.slow_on_tele)
        self.gamepads_dialog.show()
        self.gamepads_dialog.raise_()

    def _refresh_gamepads_dialog(self) -> None:
        d = self.gamepads_dialog
        if d is not None and d.isVisible():
            d.set_rows(self.pad_rows())

    def _refresh_manual_speed(self) -> None:
        """Такт окна: зум камер для «медленнее на крупном плане» и пересчёт скорости, пока стик держат."""
        for c in self.grid.cells:
            if c is None:
                continue
            if self.cfg.slow_on_tele:
                c.session.poll_zoom(ZOOM_POLL_S)
            c.mixer.refresh()

    def pad_sensitivity(self, info: dict | None) -> int:
        """Чувствительность геймпада по его опознавателю (окно «Геймпады…»); незнакомый — по умолчанию."""
        pid = (info or {}).get("id")
        return clamp_sens(self.cfg.pad_sensitivity.get(pid, DEFAULT_SENS)) if pid else DEFAULT_SENS

    def set_pad_sensitivity(self, pad_id: str, sens: int) -> None:
        """Из окна «Геймпады…»: сохранить и сразу применить к подключённым геймпадам с этим опознавателем."""
        self.cfg.pad_sensitivity[pad_id] = clamp_sens(sens)
        self._save()
        for b in self._bridges:
            if (b.info or {}).get("id") == pad_id:
                b.set_sensitivity(self.cfg.pad_sensitivity[pad_id])

    def pad_l1r1_mode(self, pad_id: str | None) -> str:
        """Что меняют L1/R1 у геймпада: "limit" — предел скорости (по умолчанию), "sens" — чувствительность."""
        return self.cfg.pad_l1r1_mode.get(pad_id, "limit") if pad_id else "limit"

    def pad_speed_gear(self, pad_id: str | None) -> int:
        """Ступень скорости 1…SPEED_GEARS; незнакомый геймпад — DEFAULT_GEAR."""
        return self.cfg.pad_gear.get(pad_id, DEFAULT_GEAR) if pad_id else DEFAULT_GEAR

    def set_pad_l1r1_mode(self, pad_id: str, mode: str) -> None:
        """Из окна «Геймпады…»: режим L1/R1; предел скорости действует только в режиме "limit"."""
        self.cfg.pad_l1r1_mode[pad_id] = "sens" if mode == "sens" else "limit"
        self._save()
        for b in self._bridges:
            if (b.info or {}).get("id") == pad_id:
                self._apply_pad_speed(b)
        self._apply_held()  # стик держат — новый предел сразу

    def _apply_pad_speed(self, b: GamepadBridge) -> None:
        pid = (b.info or {}).get("id")
        if pid is None:
            return  # без опознавателя ступень живёт только в мосте, до отключения
        gear = self.pad_speed_gear(pid) if self.pad_l1r1_mode(pid) == "limit" else DEFAULT_GEAR
        b.set_speed_limit(PAD_GEAR_DEG_S[gear - 1])

    def _pad_speed_step(self, b: GamepadBridge, d: int) -> None:
        """L1/R1: ступень предела скорости или чувствительность ±10; за краем — длинный толчок."""
        pid = (b.info or {}).get("id")
        if self.pad_l1r1_mode(pid) == "sens":
            cur = b.sensitivity
            new = clamp_sens(cur + 10 * d)
            if new == cur:
                b.rumble(RUMBLE_EDGE)
                return
            if pid:
                self.set_pad_sensitivity(pid, new)
            else:
                b.set_sensitivity(new)
            b.rumble(RUMBLE_STEP)
        else:
            cur = self.pad_speed_gear(pid) if pid else gear_of(b.speed_limit)
            new = max(1, min(SPEED_GEARS, cur + d))
            if new == cur:
                b.rumble(RUMBLE_EDGE)
                return
            if pid:
                self.cfg.pad_gear[pid] = new
                self._save()
                for x in self._bridges:  # одинаковые геймпады без серийного номера делят ступень
                    if (x.info or {}).get("id") == pid:
                        self._apply_pad_speed(x)
            else:
                b.set_speed_limit(PAD_GEAR_DEG_S[new - 1])
            b.rumble(RUMBLE_STEP)
        self._refresh_gamepads_dialog()

    def _remember_pad(self, b: GamepadBridge, info: dict) -> None:
        pid, name = info.get("id"), info.get("name") or "Геймпад"
        if pid:
            b.set_sensitivity(self.pad_sensitivity(info))
            self._apply_pad_speed(b)
            if self.cfg.pad_names.get(pid) != name:
                self.cfg.pad_names[pid] = name
                self._save()

    @staticmethod
    def _send_velocity(s, v: tuple[float, float, float]) -> None:
        s.set_pan_tilt_velocity(v[0], v[1])
        s.set_zoom_velocity(v[2])

    # --- геймпад ------------------------------------------------------

    def _pad_cell(self, pad: int | None) -> Cell | None:
        """Клетка, которой управляет геймпад n (по умолчанию — первый физический). Единственное место выбора камеры для него."""
        return self._cell_of_pad(pad)

    @property
    def _bridge_pad(self) -> int | None:
        return self._first_pad()

    def _first_pad(self) -> int | None:
        """Номер геймпада первого моста (self.pad); None — он не подключён."""
        return self._pad_nums.get(self.pad)

    def _cell_of_pad(self, pad: int | None) -> Cell | None:
        slot = self.pad_router.slot_of(pad) if pad is not None else None
        return self.grid.cells[slot] if slot is not None else None

    def _pad_session(self, pad: int | None) -> CameraSession | None:
        c = self._pad_cell(pad)
        return c.session if c else None

    # --- точки входа геймпадов (поток Qt); мост зовёт их для своего номера в маршрутизаторе ---

    def pad_connected(self, bridge: bool | GamepadBridge = False) -> int:
        """Геймпад подключился: берёт первую свободную камеру. Возвращает его номер 1–4.

        `bridge` — мост физического геймпада (True — первый, self.pad): его вибрация и огоньки идут через него."""
        b = self.pad if bridge is True else (bridge or None)
        pad, moves, fb = self.pad_router.connect()
        if b is not None:
            self.pads[pad] = b  # до обработки движений: _pad_moved/_pad_feedback отличают физический геймпад
            self._pad_nums[b] = pad
        # подключение — не переход: автомат моста сам заперт, если стик отклонён при подключении; второй замок
        # отсюда (поток Qt, позже опроса) запирал бы и стик, отклонённый уже после подключения
        self._pad_apply(moves, fb, connecting=pad)
        self._refresh_service_cells()
        return pad

    def pad_disconnected(self, pad: int) -> None:
        """Геймпад отключился: его камере — нули от его источника."""
        self._pad_apply(self.pad_router.disconnect(pad), [])
        self._pad_vel.pop(pad, None)
        b = self.pads.pop(pad, None)
        if b is not None:
            self._pad_nums.pop(b, None)
        if self._menu_pad == pad:
            self._menu_pad = None
        self._refresh_service_cells()

    def pad_velocity(self, pad: int, pan: float, tilt: float, zoom: float, focus: float = 0.0) -> None:
        """Скорость геймпада — в смеситель его камеры. Нет камеры — игнорируется."""
        self._pad_vel[pad] = (float(pan), float(tilt), float(zoom))
        c = self._cell_of_pad(pad)
        if c is not None:
            c.mixer.set(f"pad{pad}", pan, tilt, zoom, focus)

    def pad_stop(self, pad: int) -> None:
        """Стоп с тачпада: только камера этого геймпада (все её источники), слежение и разрешения агенту."""
        b = self.pads.get(pad)
        if b is not None:
            b.reset_motion()  # автомат заперт до центра; смесителю — нули, чтобы он не защёлкнул источник
            self.pad_velocity(pad, 0.0, 0.0, 0.0)
        c = self._cell_of_pad(pad)
        if c is None:
            return
        c.gate.revoke()
        c.mixer.clear_all()
        c.session.emergency_stop()
        if c.session.limit_watch:
            c.session.limit_watch.set_demand(0.0, 0.0, 0.0)
        if self.camera_menu.is_open and self._menu_session() is c.session:
            self.camera_menu.close_menu()  # сначала стоп, потом закрытие
        self.agent_panel.refresh()

    def pad_create(self, pad: int) -> None:
        """Create: геймпад на следующую камеру по кругу."""
        moves, fb = self.pad_router.create(pad)
        self._pad_apply(moves, fb)
        self._refresh_service_cells()

    def pad_session(self, pad: int) -> CameraSession | None:
        c = self._cell_of_pad(pad)
        return c.session if c else None

    def _pad_sync_slots(self) -> None:
        """Открытые клетки изменились: маршрутизатор переводит геймпады закрытых."""
        moves, fb = self.pad_router.set_slots([c is not None for c in self.grid.cells])
        self._pad_apply(moves, fb)

    def _pad_apply(self, moves: list[PadMove], fb: list[PadFeedback], connecting: int | None = None) -> None:
        for m in moves:
            self._pad_moved(m, connecting)
        for f in fb:
            self._pad_feedback(f)
        for i, cell in enumerate(self.cells):
            cell.set_pads(len(self.pad_router.pads_on(i)))
        if moves:
            self._sync_pad_player()

    def _pad_hold_on_new(self, pad: int, cell: Cell) -> None:
        """Геймпад пришёл к камере со стиком не в центре: она поедет, только когда стик вернётся в центр."""
        b = self.pads.get(pad)
        if b is not None:  # физический: запирает автомат моста (нули в смеситель дойдут сами)
            b.reset_motion()
            b.cancel_holds()
        elif any(self._pad_vel.get(pad, ())):
            cell.mixer.latch(f"pad{pad}")

    def _pad_moved(self, m: PadMove, connecting: int | None = None) -> None:
        old = self.grid.cells[m.old_slot] if m.old_slot is not None else None
        new = self.grid.cells[m.new_slot] if m.new_slot is not None else None
        b = self.pads.get(m.pad)
        if old is not None:
            old.mixer.release(f"pad{m.pad}")  # прежней камере — нули от этого геймпада; остальные источники едут
            if old.session.limit_watch and b is not None:
                old.session.limit_watch.set_demand(0.0, 0.0, 0.0)
        if self.camera_menu.is_open and self._menu_pad == m.pad:
            self.camera_menu.close_menu()
        if new is not None:
            if m.pad != connecting:
                self._pad_hold_on_new(m.pad, new)
        elif b is not None:
            b.reset_motion()

    def _pad_feedback(self, f: PadFeedback) -> None:
        b = self.pads.get(f.pad)
        if b is not None:
            if f.kind == "dots":
                b.set_player(f.value or None)
            elif f.kind == "rumble_taken":
                b.rumble(RUMBLE_TAKEN)
            elif f.kind == "rumble_nowhere":
                b.rumble(RUMBLE_NOWHERE)
        if self.on_pad_feedback is not None:
            self.on_pad_feedback(f)

    def _on_pad_connection(self, info, bridge: GamepadBridge | None = None) -> None:
        b = bridge if bridge is not None else self.pad
        if info is not None:
            self._remember_pad(b, info)
            if b not in self._pad_nums:
                try:
                    self.pad_connected(bridge=b)
                except ValueError as e:
                    log.warning("%s", e)
        else:
            self._pad_disconnect_bridge(b)
        self._sync_pad_player()
        self._update_pad_chip()
        self._refresh_gamepads_dialog()

    def _pad_disconnect_bridge(self, b: GamepadBridge) -> None:
        n = self._pad_nums.get(b)
        if n is not None:
            self.pad_disconnected(n)

    def _on_pad_intents(self, intents: list, pad: int | None = -1, bridge: GamepadBridge | None = None) -> None:
        """Намерения геймпада n (поток Qt; -1 — первый; None — мост без номера): те же пути, что клавиатура и кнопки окна.

        `bridge` — мост-отправитель. Мост без номера (5-й геймпад, хвост после отключения) ничего не делает
        и никогда не действует от имени другого геймпада."""
        if pad == -1:
            pad = self._first_pad()
        if pad is None:
            if bridge is not None and any(isinstance(it, Disconnected) for it in intents):
                bridge.zero_velocity()
            return
        b = bridge if bridge is not None else (self.pads.get(pad) or self.pad)
        moved = False
        for it in intents:
            if isinstance(it, (PanTilt, Zoom)):
                moved = True
            elif isinstance(it, Disconnected):
                # отключили: камера стоит — ничего не шлём; едет — нули. Слежение не трогаем
                if self._menu_pad in (None, pad):
                    self.camera_menu.stop_focus()
                b.zero_velocity()
                self._pad_disconnect_bridge(b)  # его камере уходят нули; номер освобождается
                moved = False
            elif isinstance(it, Stop):
                if pad is not None:
                    self.pad_stop(pad)  # только камера этого геймпада; Esc останавливает все
                moved = False
            elif isinstance(it, ToggleFollow):
                if self._pad_on_selected(pad):
                    self.toggle_follow()
            elif isinstance(it, Composition):
                if self._pad_on_selected(pad):
                    self._pad_composition(it.d, pad)
            elif isinstance(it, SpeedStep):
                self._pad_speed_step(b, it.d)  # настройка самого геймпада: выбор клетки не нужен
                moved = moved or any(b.velocity()[:2])  # стик держат — новый предел сразу
            elif isinstance(it, NextCamera):
                if pad is not None:
                    self.pad_create(pad)
            elif isinstance(it, SaveMoment):
                self.save_moment("геймпад")  # запись — всего окна, не камеры
                b.rumble(RUMBLE_PRESET_STORED)
            elif isinstance(it, MenuOpen):
                self._pad_menu_open(pad)
            elif isinstance(it, (MenuNav, MenuAccept, MenuClose)):
                if self._menu_pad not in (None, pad):
                    continue  # меню открыто другим геймпадом
                if isinstance(it, MenuNav):
                    self.camera_menu.nav(it.dx, it.dy)
                elif isinstance(it, MenuAccept):
                    self.camera_menu.accept()
                else:
                    self.camera_menu.close_menu()
            else:
                self._pad_camera_intent(it, pad)
        if moved:
            self._apply_held()
            ps = self._pad_session(pad)
            if ps is not None and ps.limit_watch:
                ps.limit_watch.set_demand(*b.velocity())

    def _pad_rumble(self, pad: int | None, pattern) -> None:
        b = self.pads.get(pad) if pad is not None else None
        (b or self.pad).rumble(pattern)

    def _pad_camera_intent(self, it, pad: int | None) -> None:
        s = self._pad_session(pad)
        if s is None:
            return
        if isinstance(it, Preset):
            if it.store:
                s.store_preset(it.n)
                self._pad_rumble(pad, RUMBLE_PRESET_STORED)
            else:
                s.recall_preset(it.n)
        elif isinstance(it, IrisStep):
            if not s.iris_step(it.d):
                self._pad_rumble(pad, RUMBLE_NOWHERE)
        elif isinstance(it, GainStep):
            step = getattr(s, "gain_step", None)
            if step is None or not step(it.d):
                self._pad_rumble(pad, RUMBLE_NOWHERE)
        elif isinstance(it, Neighbor):
            a = s.analyzer
            if a is None or a.select_neighbor(it.d) is None:
                self._pad_rumble(pad, RUMBLE_NOWHERE)
            else:
                self._update_status()
        elif isinstance(it, PushFocus):
            s.push_focus()

    def _pad_on_selected(self, pad: int | None) -> bool:
        """Слежение и крупность живут в правой панели выбранной камеры: с геймпада — только когда он на ней."""
        c = self._pad_cell(pad)
        ok = c is not None and c is self.selected_cell
        if not ok and c is not None:
            self._pad_rumble(pad, RUMBLE_NOWHERE)
        return ok

    def _pad_menu_open(self, pad: int | None) -> None:
        """Меню Options с геймпада: на его камере, поверх её видео."""
        c = self._pad_cell(pad)
        if c is None or self.camera_menu.is_open:
            return
        self._menu_pad = pad
        self._menu_to(self.cells[self.grid.cells.index(c)].video_frame)
        self.camera_menu.open()

    def _pad_stop_motion(self) -> None:
        """Esc и закрытие окна: все геймпады — нули и автомат заперт до центра, опрос упора стоит.
        Смесители камер обнуляет grid.emergency_stop_all / close_all."""
        for b in self._bridges:
            b.reset_motion()
        for c in self.grid.cells:
            if c is not None and c.session.limit_watch:
                c.session.limit_watch.set_demand(0.0, 0.0, 0.0)

    def _pad_composition(self, d: int, pad: int | None) -> None:
        """Только крупность: пояс ↔ рост ↔ общий. Свайп вправо (−1) — шире, к «Общему»; влево (+1) — ближе;
        за краем — «некуда».
        В пояс из роста — на последнюю сторону пояса (слева/центр/справа)."""
        cam = self._current_cfg()
        if cam is None:
            return
        cur = cam.composition if cam.composition in COMPOSITIONS else "waist_center"
        i = _PAD_SIZES.index("waist" if cur.startswith("waist") else cur)
        j = i - d
        if not 0 <= j < len(_PAD_SIZES):
            self._pad_rumble(pad, RUMBLE_NOWHERE)
            return
        self._on_composition(self._last_waist if _PAD_SIZES[j] == "waist" else _PAD_SIZES[j])

    def show_hotkeys(self) -> None:
        if self.hotkeys_dialog is None:
            self.hotkeys_dialog = HotkeysDialog(parent=self)
        self.hotkeys_dialog.show()
        self.hotkeys_dialog.raise_()

    def _chip_bridge(self) -> GamepadBridge | None:
        """Чип показывает первый подключённый геймпад (остальные — в служебной клетке)."""
        return next((b for b in self._bridges if b.connected), None)

    def _update_pad_chip(self) -> None:
        """Чип «номер · заряд · сколько осталось»; без геймпада скрыт."""
        b = self._chip_bridge()
        if b is None:
            self.pad_chip.hide()
            self._fit_top_bar()
            return
        num = self._pad_slot_number(self._pad_nums.get(b))
        parts = [str(num)] if num is not None else []
        pct, kind = b.battery_percent, "ok"
        if pct is not None:
            parts.append(f"{pct}%")
            if b.battery_charging:
                parts.append("заряжается")
            else:
                rem = b.battery_remaining_s()
                parts.append("считаю…" if rem is None else fmt_remaining(rem))
                if pct <= 20:
                    kind = "warn"
        elif b.battery_charging:
            parts.append("заряжается")
        name = (b.info or {}).get("name") or "Геймпад"
        tip = f"{name}: огоньки на геймпаде показывают номер клетки камеры" + (f"\nЗаряд {pct}%" if pct is not None else "")
        self.pad_chip.set(" · ".join(parts), kind, tip)
        self.pad_chip.show()
        self._fit_top_bar()

    def _sync_pad_player(self) -> None:
        """Огоньки игрока = номер клетки камеры геймпада; без камеры — погасить."""
        for b in self._bridges:
            b.set_player(self._pad_slot_number(self._pad_nums.get(b)))
        self._pad_limit_binding()
        self._update_pad_chip()

    def _pad_slot_number(self, pad: int | None) -> int | None:
        c = self._pad_cell(pad)
        return self.grid.cells.index(c) + 1 if c is not None else None

    def _sync_pad(self) -> None:
        """Цвет полосы из снимков (сеть не ждём); мост шлёт его, только если цвет изменился."""
        for b in self._bridges:
            if not b.available:
                continue
            n = self._pad_nums.get(b)
            s = self._pad_session(n)
            inp = atem_input_of(s.cfg) if s is not None else 0
            state = self.atem.tally(inp) if self.atem is not None and inp else None
            f = s.follower if s is not None else None
            b.set_status(PadStatus(
                on_program=state == "pgm",
                on_preview=state == "pvw",
                link_ok=bool(s and s.online),
                standby=getattr(s, "power", None) in ("standby", "switching"),
                menu_open=self.camera_menu.is_open and self._menu_session() is s and s is not None,
                following=bool(f and f.enabled),
            ))

    def release_all_held(self) -> None:
        """Отпустить клавиши и мышь; скорость, которую держит геймпад, остаётся."""
        if self._held:
            self._held.clear()
            self._apply_held()
        self.controls.release_all()

    def changeEvent(self, e):  # noqa: N802
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.camera_menu.stop_focus()
            self.release_all_held()
        super().changeEvent(e)

    def closeEvent(self, e):  # noqa: N802
        if self._api:
            self._api.stop()
            self._api = None
        QApplication.instance().removeEventFilter(self)
        self._status_timer.stop()
        self._hide_folder_timer.stop()
        self._follow_timer.stop()
        if self.atem:
            self.atem.stop()
            self.atem = None
        for b in self._bridges:
            b.stop(timeout=0.1)  # до остановки сессии; Qt не ждёт SDL: зависший поток доделает выход сам
        if self._pad_multi is not None:
            self._pad_multi.stop(timeout=0.1)
        self._pad_stop_motion()
        self.release_all_held()
        for d in list(self._cell_dialogs.values()):
            d.reject()
        self.grid.close_all()
        self._save()
        super().closeEvent(e)
