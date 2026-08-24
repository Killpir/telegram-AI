from aiogram.fsm.state import State, StatesGroup


class AdminInput(StatesGroup):
    waiting = State()


class BroadcastDraft(StatesGroup):
    content = State()
    audience = State()


class PromoCreate(StatesGroup):
    name = State()
    code = State()
    credits = State()
    scope = State()
    plan = State()
    days = State()
    max_activations = State()
    per_user_limit = State()
    confirm = State()
