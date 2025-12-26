import importlib.util
import sys
import types


def _ensure_module(name: str, module: types.ModuleType) -> None:
    sys.modules[name] = module


def _install_telegram_stubs() -> None:
    if importlib.util.find_spec("telegram") is not None:
        return

    telegram = types.ModuleType("telegram")
    telegram.__path__ = []

    class _TelegramObject:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _InlineKeyboardButton:
        def __init__(self, text: str, callback_data: str | None = None):
            self.text = text
            self.callback_data = callback_data

    class _InlineKeyboardMarkup:
        def __init__(self, buttons):
            self.inline_keyboard = buttons

    class _Update(_TelegramObject):
        pass

    telegram.Bot = object
    telegram.User = _TelegramObject
    telegram.Update = _Update
    telegram.InlineKeyboardButton = _InlineKeyboardButton
    telegram.InlineKeyboardMarkup = _InlineKeyboardMarkup

    error_module = types.ModuleType("telegram.error")
    telegram.error = error_module
    error_module.TelegramError = Exception

    ext_module = types.ModuleType("telegram.ext")
    ext_module.__path__ = []

    class _DummyUpdater:
        def __init__(self) -> None:
            self.running = False

        async def start_polling(self) -> None:
            self.running = True

        async def stop(self) -> None:
            self.running = False

    class _DummyApplication:
        def __init__(self) -> None:
            self.bot = object()
            self.running = False
            self.post_init = None
            self.post_stop = None
            self.post_shutdown = None
            self.updater = _DummyUpdater()

        async def initialize(self) -> None:
            return None

        async def start(self) -> None:
            self.running = True

        async def stop(self) -> None:
            self.running = False

        async def shutdown(self) -> None:
            return None

        def add_handler(self, handler) -> None:  # pragma: no cover - best effort stub
            return None

    class _DummyBuilder:
        def __init__(self) -> None:
            self._token = None

        def token(self, token: str) -> "_DummyBuilder":
            self._token = token
            return self

        def build(self) -> _DummyApplication:
            return _DummyApplication()

    class _SimpleHandler:
        def __init__(self, *args, **kwargs) -> None:
            return None

    ext_module.Application = _DummyApplication
    ext_module.ApplicationBuilder = _DummyBuilder
    ext_module.CallbackQueryHandler = _SimpleHandler
    ext_module.CommandHandler = _SimpleHandler
    ext_module.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object())

    telegram.ext = ext_module

    _ensure_module("telegram", telegram)
    _ensure_module("telegram.error", error_module)
    _ensure_module("telegram.ext", ext_module)
    # Backwards compatibility with older imports used in some tests.
    _ensure_module("telebot", telegram)
    _ensure_module("telebot.error", error_module)
    _ensure_module("telebot.ext", ext_module)


def _install_apscheduler_stubs() -> None:
    try:
        spec = importlib.util.find_spec("apscheduler.schedulers.asyncio")
    except ModuleNotFoundError:
        spec = None
    if spec is not None:
        return

    apscheduler = types.ModuleType("apscheduler")
    apscheduler.__path__ = []
    schedulers = types.ModuleType("apscheduler.schedulers")
    schedulers.__path__ = []
    asyncio_module = types.ModuleType("apscheduler.schedulers.asyncio")

    class _DummyScheduler:
        def __init__(self, event_loop=None) -> None:
            self.event_loop = event_loop
            self.jobs = []

        def add_job(self, func, trigger, seconds: int) -> None:
            self.jobs.append((func, trigger, seconds))

        def start(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            return None

    asyncio_module.AsyncIOScheduler = _DummyScheduler

    schedulers.asyncio = asyncio_module

    apscheduler.schedulers = schedulers

    _ensure_module("apscheduler", apscheduler)
    _ensure_module("apscheduler.schedulers", schedulers)
    _ensure_module("apscheduler.schedulers.asyncio", asyncio_module)


_install_telegram_stubs()
_install_apscheduler_stubs()
