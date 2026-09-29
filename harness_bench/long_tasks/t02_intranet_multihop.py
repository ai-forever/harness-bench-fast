"""long_02_intranet_multihop — многошаговые вопросы по статическому интранету.

Мир — модель компании (люди, команды, сервисы, события с датами вступления в силу).
Все страницы интранета рендерятся из модели, ответы вычисляются запросами к модели.
Ловушки (все объяснены правилами в questions.md): дата вступления в силу ≠ дата
подписания, приказы с изменёнными датами и отменённые приказы, будущие приказы,
временное исполнение обязанностей, переименования команд и смена кодовых имён,
устаревшие справочные страницы, замены в графике дежурств, смена дежурства
в понедельник в 10:00, исправления к отчётам об инцидентах.
"""

from __future__ import annotations

import functools
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .common import long_task, norm, num, read_json, require_share, rng, write, write_json

TASK_ID = "long_02_intranet_multihop"
SNAPSHOT = date(2026, 6, 30)
START = date(2024, 1, 1)
ROT_START = date(2024, 12, 30)  # понедельник; графики дежурств с этой недели
MIN_CORRECT = 36
N_QUESTIONS = 40

# --------------------------------------------------------------------------
# Даты
# --------------------------------------------------------------------------

MONTHS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
            "сентября", "октября", "ноября", "декабря"]
MONTHS_N = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
            "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_P = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
            "сентябре", "октябре", "ноябре", "декабре"]
WEEKDAYS_N = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_A = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]


def dd(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def dlong(d: date) -> str:
    return f"{d.day} {MONTHS_G[d.month - 1]} {d.year} г."


def dlong2(d: date) -> str:
    return f"{d.day} {MONTHS_G[d.month - 1]} {d.year} года"


def dshort(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}"


def dany(R, d: date) -> str:
    return R.choice([dd, dd, dlong, dlong2])(d)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def rdate(R, a: date, b: date) -> date:
    return a + timedelta(days=R.randint(0, (b - a).days))


# --------------------------------------------------------------------------
# Морфология
# --------------------------------------------------------------------------

_SOFT = "гкхжшчщ"
_SIBIL = "жшчщц"
CASES = "ngdaip"


def sur_form(base: str, stype: str, fem: bool, case: str) -> str:
    if stype == "ind" or case == "n" and not fem:
        if stype == "sky" and case == "n":
            return base + "ий"
        return base
    if stype == "ov":
        if not fem:
            return base + {"g": "а", "d": "у", "a": "а", "i": "ым", "p": "е"}[case]
        return base + {"n": "а", "g": "ой", "d": "ой", "a": "у", "i": "ой", "p": "ой"}[case]
    # -ский
    if not fem:
        return base + {"g": "ого", "d": "ому", "a": "ого", "i": "им", "p": "ом"}[case]
    return base + {"n": "ая", "g": "ой", "d": "ой", "a": "ую", "i": "ой", "p": "ой"}[case]


_NAME_IRREG = {
    "Павел": ("Павла", "Павлу", "Павла", "Павлом", "Павле"),
    "Пётр": ("Петра", "Петру", "Петра", "Петром", "Петре"),
    "Илья": ("Ильи", "Илье", "Илью", "Ильёй", "Илье"),
    "Лев": ("Льва", "Льву", "Льва", "Львом", "Льве"),
}


def name_form(n: str, fem: bool, case: str) -> str:
    if case == "n":
        return n
    k = "gdaip".index(case)
    if n in _NAME_IRREG:
        return _NAME_IRREG[n][k]
    if n.endswith("ия"):
        st = n[:-1]
        return st + ("и", "и", "ю", "ей", "и")[k]
    if n.endswith("я"):
        st = n[:-1]
        return st + ("и", "е", "ю", "ей", "е")[k]
    if n.endswith("а"):
        st = n[:-1]
        g = "и" if st[-1] in _SOFT else "ы"
        i = "ей" if st[-1] in _SIBIL else "ой"
        return st + (g, "е", "у", i, "е")[k]
    if n.endswith("ий"):
        st = n[:-2]
        return st + ("ия", "ию", "ия", "ием", "ии")[k]
    if n[-1] in "йь":
        st = n[:-1]
        return st + ("я", "ю", "я", "ем", "е")[k]
    return n + ("а", "у", "а", "ом", "е")[k]


def patr_form(pt: str, fem: bool, case: str) -> str:
    if case == "n":
        return pt
    k = "gdaip".index(case)
    if fem:
        return pt[:-1] + ("ы", "е", "у", "ой", "е")[k]
    return pt + ("а", "у", "а", "ем", "е")[k]


INDECL = {"Бетельгейзе"}


def wform(word: str, case: str) -> str:
    """Склонение одиночного названия (команды, кодовые имена) в кавычках."""
    if case == "n" or word in INDECL or " " in word or "-" in word:
        return word
    k = "gdaip".index(case)
    last = word[-1]
    if last == "а":
        st = word[:-1]
        g = "и" if st[-1] in _SOFT else "ы"
        return st + (g, "е", "у", "ой", "е")[k]
    if last == "я":
        st = word[:-1]
        return st + ("и", "е", "ю", "ей", "е")[k]
    if last in "йь":
        st = word[:-1]
        return (st + "я", st + "ю", word, st + "ем", st + "е")[k]
    ins = "ем" if last in _SIBIL else "ом"
    return (word + "а", word + "у", word, word + ins, word + "е")[k]


def q(word: str, case: str = "n") -> str:
    return f"«{wform(word, case)}»"


def gfill(text: str, p) -> str:
    """Подставить родовые формы вида [пришёл|пришла] по полу сотрудника."""
    return re.sub(r"\[([^|\]]*)\|([^\]]*)\]", lambda m: m.group(2) if p.fem else m.group(1), text)


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"], strict=True))


def translit(s: str) -> str:
    return "".join(_TR.get(c, c) for c in s.lower()).replace(" ", "-")


# --------------------------------------------------------------------------
# Банки имён
# --------------------------------------------------------------------------

SURNAMES_OV = ["Смирнов", "Кузнецов", "Попов", "Соколов", "Лебедев", "Козлов", "Новиков", "Морозов", "Волков", "Соловьёв", "Васильев", "Зайцев", "Павлов", "Семёнов", "Голубев", "Виноградов", "Богданов", "Воробьёв", "Фёдоров", "Михайлов", "Беляев", "Тарасов", "Белов", "Комаров", "Орлов", "Киселёв", "Макаров", "Андреев", "Ковалёв", "Ильин", "Гусев", "Титов", "Кузьмин", "Кудрявцев", "Баранов", "Куликов", "Алексеев", "Степанов", "Яковлев", "Сорокин", "Сергеев", "Романов", "Захаров", "Борисов", "Королёв", "Герасимов", "Пономарёв", "Григорьев", "Лазарев", "Медведев", "Ершов", "Никитин", "Соболев", "Рябов", "Поляков", "Цветков", "Данилов", "Жуков", "Фролов", "Журавлёв", "Николаев", "Крылов", "Максимов", "Осипов", "Белоусов", "Федотов", "Дорофеев", "Егоров", "Матвеев", "Бобров", "Дмитриев", "Калинин", "Анисимов", "Петухов", "Антонов", "Тимофеев", "Никифоров", "Веселов", "Филиппов", "Марков", "Большаков", "Суханов", "Миронов", "Ширяев", "Коновалов", "Шестаков", "Казаков", "Ефимов", "Денисов", "Громов", "Фомин", "Давыдов", "Мельников", "Щербаков", "Блинов", "Колесников", "Карпов", "Афанасьев", "Власов", "Маслов", "Исаков", "Тихонов", "Аксёнов", "Гаврилов", "Родионов", "Котов", "Горбунов", "Кудряшов", "Быков", "Зуев", "Третьяков", "Савельев", "Панов", "Рыбаков", "Суворов", "Абрамов", "Воронов", "Мухин", "Архипов", "Трофимов", "Мартынов", "Емельянов", "Горшков", "Чернов", "Овчинников", "Селезнёв", "Панфилов", "Копылов", "Михеев", "Галкин", "Назаров", "Лобанов", "Лукин", "Беляков", "Потапов", "Некрасов", "Хохлов", "Жданов", "Наумов", "Шилов", "Воронцов", "Ермаков", "Дроздов", "Игнатьев", "Савин", "Логинов", "Сафонов", "Капустин", "Кириллов", "Моисеев", "Елисеев", "Кошелев", "Костин", "Орехов", "Ефремов", "Исаев", "Евдокимов", "Калашников", "Кабанов", "Носков", "Юдин", "Кулагин", "Лапин", "Прохоров", "Нестеров", "Харитонов", "Агафонов", "Муравьёв", "Ларионов", "Федосеев", "Зимин", "Пахомов", "Шубин", "Игнатов", "Филатов", "Крюков", "Рогов", "Кулаков", "Терентьев", "Молчанов"]
SURNAMES_SKY = ["Островск", "Покровск", "Вишневск", "Лозинск", "Ржевск", "Заславск", "Корецк"]
SURNAMES_IND = ["Шевченко", "Бондаренко", "Ткаченко", "Коваленко", "Черных", "Седых", "Долгих"]

MALE_NAMES = ["Александр", "Алексей", "Андрей", "Антон", "Артём", "Борис", "Вадим", "Виктор", "Владимир", "Глеб", "Григорий", "Денис", "Дмитрий", "Евгений", "Егор", "Иван", "Игорь", "Илья", "Кирилл", "Константин", "Леонид", "Максим", "Михаил", "Никита", "Николай", "Олег", "Павел", "Роман", "Руслан", "Семён", "Сергей", "Станислав", "Степан", "Тимур", "Фёдор", "Юрий", "Ярослав", "Георгий", "Арсений", "Матвей"]
FEMALE_NAMES = ["Анна", "Алина", "Анастасия", "Валерия", "Варвара", "Вера", "Виктория", "Галина", "Дарья", "Екатерина", "Елена", "Елизавета", "Ирина", "Кира", "Ксения", "Лариса", "Людмила", "Марина", "Мария", "Надежда", "Наталья", "Нина", "Ольга", "Полина", "Светлана", "Софья", "Татьяна", "Юлия", "Яна", "Вероника", "Алёна", "Евгения", "Маргарита", "Оксана", "Тамара"]
PATR = [
    ("Александрович", "Александровна"), ("Алексеевич", "Алексеевна"), ("Андреевич", "Андреевна"),
    ("Антонович", "Антоновна"), ("Борисович", "Борисовна"), ("Вадимович", "Вадимовна"),
    ("Викторович", "Викторовна"), ("Владимирович", "Владимировна"), ("Геннадьевич", "Геннадьевна"),
    ("Григорьевич", "Григорьевна"), ("Денисович", "Денисовна"), ("Дмитриевич", "Дмитриевна"),
    ("Евгеньевич", "Евгеньевна"), ("Игоревич", "Игоревна"), ("Константинович", "Константиновна"),
    ("Леонидович", "Леонидовна"), ("Максимович", "Максимовна"), ("Михайлович", "Михайловна"),
    ("Николаевич", "Николаевна"), ("Олегович", "Олеговна"), ("Павлович", "Павловна"),
    ("Петрович", "Петровна"), ("Романович", "Романовна"), ("Сергеевич", "Сергеевна"),
    ("Станиславович", "Станиславовна"), ("Степанович", "Степановна"), ("Фёдорович", "Фёдоровна"),
    ("Юрьевич", "Юрьевна"), ("Валерьевич", "Валерьевна"), ("Анатольевич", "Анатольевна"),
    ("Васильевич", "Васильевна"), ("Витальевич", "Витальевна"), ("Аркадьевич", "Аркадьевна"),
    ("Эдуардович", "Эдуардовна"), ("Тимофеевич", "Тимофеевна"),
]


@dataclass
class Person:
    pid: int
    base: str
    stype: str
    name: str
    patr: str
    fem: bool
    hired: date
    slug: str = ""
    email: str = ""
    room: int = 0
    skills: list = field(default_factory=list)
    about: list = field(default_factory=list)
    role: str = ""  # особая роль (директор и т. п.)

    def sur(self, case: str = "n") -> str:
        return sur_form(self.base, self.stype, self.fem, case)

    def full(self, case: str = "n") -> str:
        return f"{self.sur(case)} {name_form(self.name, self.fem, case)} {patr_form(self.patr, self.fem, case)}"

    def fio2(self, case: str = "n") -> str:
        return f"{name_form(self.name, self.fem, case)} {patr_form(self.patr, self.fem, case)} {self.sur(case)}"

    def init(self, case: str = "n") -> str:
        return f"{self.sur(case)} {self.name[0]}. {self.patr[0]}."

    def init2(self, case: str = "n") -> str:
        return f"{self.name[0]}. {self.patr[0]}. {self.sur(case)}"

    def short(self, case: str = "n") -> str:
        return f"{name_form(self.name, self.fem, case)} {self.sur(case)}"

    def io(self, case: str = "n") -> str:
        return f"{name_form(self.name, self.fem, case)} {patr_form(self.patr, self.fem, case)}"

    def him(self, case: str = "a") -> str:
        m = {"a": ("его", "её"), "g": ("его", "её"), "d": ("ему", "ей"), "n": ("он", "она"),
             "i": ("им", "ей")}
        return m[case][self.fem]


# --------------------------------------------------------------------------
# Банки предметной области
# --------------------------------------------------------------------------

TEAM_NAMES = ["Альфа", "Бриз", "Гранит", "Дельта", "Енисей", "Жемчуг", "Зенит", "Импульс", "Кварц", "Лагуна", "Меридиан", "Норд", "Омега", "Пульс", "Радуга", "Сигма", "Титан", "Ураган", "Феникс", "Ирбис"]
RENAME_POOL = ["Орбита", "Квант", "Атлас", "Горизонт", "Сфера", "Прибой", "Эталон", "Маяк", "Контур", "Спектр", "Фарватер", "Причал"]
DEPTS = ["Платформа", "Платежи", "Логистика", "Данные"]
DEPT_SLUG = {"Платформа": "platforma", "Платежи": "platezhi", "Логистика": "logistika", "Данные": "dannye"}

CITIES = ["Москва", "Санкт-Петербург", "Казань", "Новосибирск"]
CITY_P = {"Москва": "Москве", "Санкт-Петербург": "Санкт-Петербурге", "Казань": "Казани",
          "Новосибирск": "Новосибирске"}
CITY_A = {"Москва": "Москву", "Санкт-Петербург": "Санкт-Петербург", "Казань": "Казань",
          "Новосибирск": "Новосибирск"}
CITY_ADJ = {
    "Москва": ("московский", "московского", "московскому", "московский", "московским", "московском"),
    "Санкт-Петербург": ("петербургский", "петербургского", "петербургскому", "петербургский",
                        "петербургским", "петербургском"),
    "Казань": ("казанский", "казанского", "казанскому", "казанский", "казанским", "казанском"),
    "Новосибирск": ("новосибирский", "новосибирского", "новосибирскому", "новосибирский",
                    "новосибирским", "новосибирском"),
}
CITY_ADDR = {"Москва": "ул. Лесная, 7", "Санкт-Петербург": "Малый пр. В. О., 54",
             "Казань": "ул. Петербургская, 52", "Новосибирск": "ул. Кирова, 44"}
CITY_PHONE = {"Москва": 2000, "Санкт-Петербург": 3000, "Казань": 5000, "Новосибирск": 7000}
CITY_SLUG = {"Москва": "moskva", "Санкт-Петербург": "spb", "Казань": "kazan", "Новосибирск": "novosibirsk"}


def cadj(city: str, case: str) -> str:
    return CITY_ADJ[city][CASES.index(case)]


# (официальное имя, дирекция, что делает)
SERVICES = [
    ("Сервис расчёта тарифов", "Логистика", "рассчитывает стоимость доставки по весу, габаритам и тарифной зоне"),
    ("Платёжный шлюз", "Платежи", "принимает платежи клиентов и маршрутизирует их к банкам-эквайерам"),
    ("Сервис уведомлений", "Платформа", "рассылает SMS, push-уведомления и письма клиентам и курьерам"),
    ("Каталог товаров партнёров", "Данные", "хранит карточки товаров партнёров и их атрибуты"),
    ("Сервис авторизации", "Платформа", "выдаёт токены доступа и проверяет права пользователей"),
    ("Хранилище документов", "Платформа", "хранит сканы накладных, актов и договоров"),
    ("Сервис геокодирования", "Логистика", "переводит адреса в координаты и обратно"),
    ("Планировщик маршрутов", "Логистика", "строит маршруты курьеров с учётом окон доставки"),
    ("Биллинг партнёров", "Платежи", "выставляет счета партнёрам и начисляет комиссии"),
    ("Сервис отчётности", "Данные", "собирает регламентные отчёты для партнёров и бухгалтерии"),
    ("Шина событий", "Платформа", "доставляет события между сервисами компании"),
    ("Витрина продаж", "Данные", "агрегирует продажи по дням, регионам и партнёрам"),
    ("Антифрод-движок", "Платежи", "оценивает риск транзакций и блокирует подозрительные"),
    ("Сервис профилей клиентов", "Платформа", "хранит профили, адреса и согласия клиентов"),
    ("Складской учёт", "Логистика", "ведёт остатки и движение товаров на складах"),
    ("Трекинг отправлений", "Логистика", "собирает статусы посылок и показывает их клиентам"),
    ("Сервис промокодов", "Платежи", "выпускает и проверяет промокоды и скидки"),
    ("Сервис договоров", "Платежи", "формирует и подписывает договоры с партнёрами"),
    ("Бэкенд приложения курьера", "Логистика", "обслуживает мобильное приложение курьеров"),
    ("Бэкенд кабинета партнёра", "Платформа", "обслуживает веб-кабинет партнёров"),
    ("Поисковый сервис", "Данные", "индексирует каталог и отвечает на поисковые запросы"),
    ("Сервис рекомендаций", "Данные", "подбирает товары и тарифы для клиентов"),
    ("Сервис учёта возвратов", "Логистика", "оформляет возвраты и отслеживает их движение"),
    ("Сервис сверки с банками", "Платежи", "сверяет платёжные реестры с выписками банков"),
    ("Налоговый калькулятор", "Платежи", "считает НДС и налоговые реквизиты чеков"),
    ("Сервис телеметрии", "Платформа", "собирает метрики и трассировки со всех сервисов"),
    ("Сервис флагов функциональности", "Платформа", "управляет включением функций для групп пользователей"),
    ("Бэкенд службы поддержки", "Платформа", "хранит обращения клиентов и маршрутизирует их операторам"),
    ("Сервис планирования смен", "Логистика", "планирует смены курьеров и сотрудников складов"),
    ("Архив платёжных документов", "Данные", "хранит платёжные поручения и чеки за пять лет"),
]
CODENAMES = ["Полярис", "Кассиопея", "Андромеда", "Сириус", "Альтаир", "Денеб", "Ригель", "Капелла", "Процион", "Арктур", "Антарес", "Альдебаран", "Бетельгейзе", "Мицар", "Спика", "Регул", "Фомальгаут", "Канопус", "Хадар", "Мира", "Кастор", "Поллукс", "Беллатрикс", "Альгол", "Шедар", "Мирфак", "Тубан", "Кохаб", "Садр", "Алиот"]
CODENAME_POOL = ["Менкар", "Ахернар", "Эльнат", "Мерак", "Алькор", "Сабик", "Акрукс", "Нунки"]
PROJECTS = [
    ("Полярис-2", "проект переписывания расчёта тарифов на новый движок правил; сервисом не является"),
    ("Созвездие", "программа миграции сервисов в новый кластер Kubernetes"),
    ("Ригель-Next", "исследовательский прототип, закрыт в 2025 году"),
    ("Капелла-Лайт", "облегчённый SDK для партнёров, поддерживается сообществом"),
    ("Северное сияние", "внутренний хакатон 2025 года"),
    ("Меридиан-Про", "пилот аналитики для крупных партнёров, не связан с командой «Меридиан»"),
    ("Альтаир Софт", "бывший подрядчик, к одноимённому сервису отношения не имеет"),
]


# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------


@dataclass
class Item:
    kind: str
    data: dict
    eff: date
    sign: date
    orig: date
    show_date: bool = True
    cancelled: bool = False
    order: object = None
    idx: int = 1


@dataclass
class Entry:
    key: tuple
    value: object
    item: Item | None
    seq: int


@dataclass
class Order:
    items: list
    sign: date
    no: str = ""
    subject: str = ""
    kind: str = "main"  # main | amend | cancel
    ref: object = None


@dataclass
class Subst:
    tid: int
    lead: int
    sub: int
    start: date
    end: date
    orig_end: date
    sign: date
    cancelled: bool = False
    item: Item | None = None
    reason: str = "отпуска"


@dataclass
class Incident:
    code: str
    dt: datetime
    dt_rep: datetime
    svc: int
    svc_rep: int
    sev: str
    dur: int
    author: int
    closer: int
    responders: list
    cause: int
    corr_note: str = ""  # "time" | "svc" | ""


@dataclass(frozen=True)
class Mode:
    eff: str = "true"  # true | orig | sign
    cancel: bool = True
    subst: bool = True
    swaps: bool = True
    monday: bool = True
    corr: bool = True
    snapshot: bool = False


TRUE = Mode()
NAIVE_FIRST = Mode(eff="sign", cancel=False, subst=False, swaps=False, monday=False, corr=False)
NAIVE_SNAPSHOT = Mode(snapshot=True)
NAIVE_AMEND = Mode(eff="orig", cancel=False)
NAIVE_SUBST = Mode(subst=False)
NAIVE_NOTES = Mode(swaps=False, monday=False, corr=False)
NAIVE_MODES = {"first": NAIVE_FIRST, "snapshot": NAIVE_SNAPSHOT, "amend": NAIVE_AMEND,
               "subst": NAIVE_SUBST, "notes": NAIVE_NOTES}


class World:
    def __init__(self):
        self.people: list[Person] = []
        self.team_dept: list[str] = []
        self.team_city: list[str] = []
        self.hist: dict[tuple, list[Entry]] = {}
        self.items: list[Item] = []
        self.orders: list[Order] = []
        self.substs: list[Subst] = []
        self.incidents: list[Incident] = []
        self.rota: dict[tuple, int] = {}
        self.swaps: dict[tuple, int] = {}
        self.phone_news: list[tuple] = []  # (announce, eff, pid, old, new, reason)
        self.log: list[tuple] = []  # (eff, kind, data) — истинные события для историй
        self.seq = 0

    # ---- запись ----
    def put(self, key, value, item):
        self.seq += 1
        self.hist.setdefault(key, []).append(Entry(key, value, item, self.seq))

    # ---- запросы ----
    @staticmethod
    def _d(e: Entry, mode: Mode) -> date:
        if e.item is None:
            return START
        return {"true": e.item.eff, "orig": e.item.orig, "sign": e.item.sign}[mode.eff]

    def val(self, key, d: date, mode: Mode = TRUE):
        best = None
        for e in self.hist.get(key, ()):
            if e.item is not None and e.item.cancelled and mode.cancel:
                continue
            ed = self._d(e, mode)
            if ed <= d and (best is None or (ed, e.seq) >= best[0]):
                best = ((ed, e.seq), e.value)
        return None if best is None else best[1]

    def at(self, d: date, mode: Mode) -> date:
        return SNAPSHOT if mode.snapshot else d

    def lead_actual(self, tid, d, mode=TRUE):
        if mode.subst:
            for s in self.substs:
                if s.tid != tid or (s.cancelled and mode.cancel):
                    continue
                end = s.orig_end if mode.eff != "true" else s.end
                if s.start <= d <= end:
                    return s.sub
        return self.val(("lead", tid), d, mode)

    def members(self, tid, d, mode=TRUE):
        return [p.pid for p in self.people
                if self.val(("team", p.pid), d, mode) == tid and self.val(("emp", p.pid), d, mode)]

    def owned(self, tid, d, mode=TRUE):
        return [s for s in range(len(SERVICES)) if self.val(("owner", s), d, mode) == tid]

    def inc_dt(self, inc: Incident, mode=TRUE) -> datetime:
        return inc.dt if mode.corr else inc.dt_rep

    def inc_svc(self, inc: Incident, mode=TRUE) -> int:
        return inc.svc if mode.corr else inc.svc_rep

    def oncall(self, tid, dt: datetime, mode=TRUE):
        d = dt.date()
        mon = monday_of(d)
        if mode.monday and d.weekday() == 0 and dt.hour < 10:
            mon -= timedelta(days=7)
        if mode.swaps and (tid, mon) in self.swaps:
            return self.swaps[(tid, mon)]
        return self.rota.get((tid, mon))

    def tname(self, tid, d, mode=TRUE):
        return self.val(("name", tid), d, mode)

    def code(self, sid, d, mode=TRUE):
        return self.val(("code", sid), d, mode)

    def fio(self, pid):
        return "" if pid is None else self.people[pid].full()


# --------------------------------------------------------------------------
# Генерация мира
# --------------------------------------------------------------------------


class Gen:
    def __init__(self, R):
        self.R = R
        self.W = World()
        self.used_full: set[str] = set()
        self.used_init: set[str] = set()
        self.used_phone: set[int] = set()
        self.lead: dict[int, int] = {}
        self.dep: dict[int, int] = {}
        self.team_of: dict[int, int | None] = {}
        self.emp: set[int] = set()
        self.owner: dict[int, int] = {}
        self.codes: dict[int, str] = {}
        self.names: dict[int, str] = {}
        self.phone: dict[int, int] = {}
        self.city: dict[int, str] = {}
        self.last_move: dict[int, date] = {}
        self.last_lead: dict[int, date] = {}
        self.last_owner: dict[int, date] = {}
        self.renamed: dict[int, int] = {}
        self.code_changed: set[int] = set()
        self.rename_pool = list(RENAME_POOL)
        self.code_pool = list(CODENAME_POOL)
        R.shuffle(self.rename_pool)
        R.shuffle(self.code_pool)

    # ---- люди ----
    def new_person(self, hired: date, like: Person | None = None) -> Person:
        R = self.R
        for _ in range(1000):
            if like is not None:
                fem, base, stype, name = like.fem, like.base, like.stype, like.name
            else:
                fem = R.random() < 0.45
                x = R.random()
                if x < 0.86:
                    base, stype = R.choice(SURNAMES_OV), "ov"
                elif x < 0.93:
                    base, stype = R.choice(SURNAMES_SKY), "sky"
                else:
                    base, stype = R.choice(SURNAMES_IND), "ind"
                name = R.choice(FEMALE_NAMES if fem else MALE_NAMES)
            patr = R.choice(PATR)[1 if fem else 0]
            if like is not None and patr[0] == like.patr[0]:
                continue
            p = Person(len(self.W.people), base, stype, name, patr, fem, hired)
            if p.full() in self.used_full or p.init() in self.used_init:
                continue
            if like is None and any(q.base == base for q in self.W.people) and R.random() < 0.7:
                continue
            self.used_full.add(p.full())
            self.used_init.add(p.init())
            p.slug = translit(p.sur()) + "-" + translit(p.name[0] + p.patr[0])
            p.email = f"{translit(p.name)[0]}.{translit(p.sur())}@svector.ru"
            if any(q.email == p.email for q in self.W.people):
                p.email = f"{translit(p.name)[0]}{translit(p.patr)[0]}.{translit(p.sur())}@svector.ru"
            p.room = R.randint(2, 9) * 100 + R.randint(1, 40)
            self.W.people.append(p)
            return p
        raise RuntimeError("не удалось подобрать имя")

    def new_phone(self, city: str) -> int:
        while True:
            n = CITY_PHONE[city] + self.R.randint(100, 999)
            if n not in self.used_phone:
                self.used_phone.add(n)
                return n

    # ---- приказы ----
    def order(self, items: list[Item], sign: date, kind: str = "main", ref=None) -> Order:
        o = Order(items, sign, kind=kind, ref=ref)
        for i, it in enumerate(items, 1):
            it.order, it.idx, it.sign = o, i, sign
        self.W.orders.append(o)
        self.W.items.extend(items)
        return o

    def item(self, kind, data, eff, show=True, cancelled=False) -> Item:
        return Item(kind, data, eff, eff, eff, show, cancelled)

    def sign_for(self, eff: date, show: bool, lo=4, hi=21) -> date:
        return eff - timedelta(days=self.R.randint(lo, hi)) if show else eff

    def put(self, key, value, it: Item | None):
        self.W.put(key, value, it)
        if it is not None and it.cancelled:
            return
        kind, k = key[0], key[1]
        target = {"lead": self.lead, "dep": self.dep, "team": self.team_of, "owner": self.owner,
                  "code": self.codes, "name": self.names, "phone": self.phone, "city": self.city}
        if kind == "emp":
            (self.emp.add if value else self.emp.discard)(k)
        else:
            target[kind][k] = value

    # ---- начальное состояние ----
    def initial(self):
        R, W = self.R, self.W
        homes = ["Москва"] * 11 + ["Санкт-Петербург"] * 4 + ["Казань"] * 3 + ["Новосибирск"] * 2
        R.shuffle(homes)
        for tid, nm in enumerate(TEAM_NAMES):
            W.team_dept.append(DEPTS[tid // 5])
            W.team_city.append(homes[tid])
            self.put(("name", tid), nm, None)
        # 6 человек без команд: гендиректор, директор по персоналу, 4 директора дирекций
        for role in ["ceo", "hr"] + [f"dir:{d}" for d in DEPTS]:
            p = self.new_person(rdate(R, date(2012, 1, 1), date(2019, 12, 31)))
            p.role = role
            self.put(("emp", p.pid), True, None)
            self.put(("team", p.pid), None, None)
            c = "Москва"
            self.put(("city", p.pid), c, None)
            self.put(("phone", p.pid), self.new_phone(c), None)
        likes: list[Person] = []
        for tid in range(len(TEAM_NAMES)):
            size = R.choice([5, 6, 6, 6, 7])
            pids = []
            for _ in range(size):
                like = None
                if len(likes) < 4 and W.people and R.random() < 0.06:
                    like = R.choice(W.people[6:]) if len(W.people) > 6 else None
                p = self.new_person(rdate(R, date(2014, 3, 1), date(2023, 11, 30)), like)
                if like is not None:
                    likes.append(p)
                pids.append(p.pid)
                c = W.team_city[tid] if R.random() < 0.75 else R.choice(CITIES)
                self.put(("emp", p.pid), True, None)
                self.put(("team", p.pid), tid, None)
                self.put(("city", p.pid), c, None)
                self.put(("phone", p.pid), self.new_phone(c), None)
                self.last_move[p.pid] = date(2023, 1, 1)
            self.put(("lead", tid), pids[0], None)
            self.put(("dep", tid), pids[1], None)
            self.last_lead[tid] = date(2023, 6, 1)
        for sid, (_, dept, _) in enumerate(SERVICES):
            cands = [t for t in range(len(TEAM_NAMES)) if W.team_dept[t] == dept]
            self.put(("owner", sid), R.choice(cands), None)
            self.put(("code", sid), CODENAMES[sid], None)
            self.last_owner[sid] = date(2023, 6, 1)

    # ---- вспомогательное ----
    def size(self, tid):
        return sum(1 for p, t in self.team_of.items() if t == tid and p in self.emp)

    def mem(self, tid):
        return sorted(p for p, t in self.team_of.items() if t == tid and p in self.emp)

    def plain_members(self, tid):
        return [p for p in self.mem(tid) if p != self.lead.get(tid) and p != self.dep.get(tid)]

    def is_boss(self, pid):
        return pid in self.lead.values() or pid in self.dep.values()

    def log(self, eff, kind, data):
        self.W.log.append((eff, kind, data))

    # ---- события ----
    def ev_transfer(self, d, cancelled=False, future=False):
        R = self.R
        cands = [p for p in sorted(self.emp) if self.team_of.get(p) is not None and not self.is_boss(p)
                 and (d - self.last_move.get(p, START)).days >= 120 and self.size(self.team_of[p]) >= 5]
        if not cands:
            return None
        p = R.choice(cands)
        a = self.team_of[p]
        tg = [t for t in range(len(TEAM_NAMES)) if t != a and self.size(t) < 8]
        same = [t for t in tg if self.W.team_dept[t] == self.W.team_dept[a]]
        b = R.choice(same if same and R.random() < 0.6 else tg)
        show = cancelled or future or R.random() < 0.85
        it = self.item("transfer", {"pid": p, "a": a, "b": b}, d, show, cancelled)
        self.order([it], self.sign_for(d, show, 8 if cancelled else 4, 25))
        self.put(("team", p), b, it)
        if not cancelled:
            self.last_move[p] = d
            self.log(d, "transfer", {"pid": p, "a": a, "b": b})
        return it

    def ev_lead(self, d, cancelled=False):
        R = self.R
        ts = [t for t in range(len(TEAM_NAMES)) if (d - self.last_lead[t]).days >= 200 and t in self.dep]
        if not ts:
            return None
        t = R.choice(ts)
        old = self.lead[t]
        show = cancelled or R.random() < 0.85
        if cancelled:
            cands = self.plain_members(t)
            if not cands:
                return None
            nl = R.choice(cands)
            it = self.item("lead", {"tid": t, "pid": nl, "old": old}, d, True, True)
            self.order([it], self.sign_for(d, True, 8, 25))
            self.put(("lead", t), nl, it)
            return it
        items, puts = [], []
        if R.random() < 0.5:
            nl = self.dep[t]
            nd_c = self.plain_members(t)
            if not nd_c:
                return None
            nd = R.choice(nd_c)
            it1 = self.item("lead", {"tid": t, "pid": nl, "old": old}, d, show)
            it2 = self.item("deputy", {"tid": t, "pid": nd, "old": nl}, d, show)
            items += [it1, it2]
            puts += [(("lead", t), nl, it1), (("dep", t), nd, it2)]
            self.log(d, "lead", {"tid": t, "pid": nl, "old": old})
            self.log(d, "deputy", {"tid": t, "pid": nd, "old": nl})
        else:
            cands = [p for p in sorted(self.emp) if self.team_of.get(p) not in (None, t)
                     and not self.is_boss(p) and self.size(self.team_of[p]) >= 5]
            if not cands:
                return None
            nl = R.choice(cands)
            a = self.team_of[nl]
            it0 = self.item("transfer", {"pid": nl, "a": a, "b": t}, d, show)
            it1 = self.item("lead", {"tid": t, "pid": nl, "old": old}, d, show)
            items += [it0, it1]
            puts += [(("team", nl), t, it0), (("lead", t), nl, it1)]
            self.last_move[nl] = d
            self.log(d, "transfer", {"pid": nl, "a": a, "b": t})
            self.log(d, "lead", {"tid": t, "pid": nl, "old": old})
        tg = [x for x in range(len(TEAM_NAMES)) if x != t and self.size(x) < 8]
        if R.random() < 0.6 and tg:
            b = R.choice(tg)
            it3 = self.item("transfer", {"pid": old, "a": t, "b": b}, d, show)
            puts.append((("team", old), b, it3))
            self.last_move[old] = d
            self.log(d, "transfer", {"pid": old, "a": t, "b": b})
        else:
            it3 = self.item("leave", {"pid": old, "tid": t, "last": d - timedelta(days=1)}, d, True)
            puts += [(("emp", old), False, it3), (("team", old), None, it3)]
            self.log(d, "leave", {"pid": old, "tid": t})
        items.append(it3)
        if it3.kind == "leave":
            show = True
            for it in items:
                it.show_date = True
        self.order(items, self.sign_for(d, show))
        for key, v, it in puts:
            self.put(key, v, it)
        self.last_lead[t] = d
        return items[0]

    def ev_deputy(self, d, future=False):
        ts = [t for t in range(len(TEAM_NAMES)) if self.plain_members(t)
              and (d - self.last_lead[t]).days >= 60]
        if not ts:
            return None
        t = self.R.choice(ts)
        nd = self.R.choice(self.plain_members(t))
        show = future or self.R.random() < 0.85
        it = self.item("deputy", {"tid": t, "pid": nd, "old": self.dep[t]}, d, show)
        self.order([it], self.sign_for(d, show))
        self.put(("dep", t), nd, it)
        if not future:
            self.log(d, "deputy", {"tid": t, "pid": nd, "old": it.data["old"]})
            self.last_lead[t] = max(self.last_lead[t], d - timedelta(days=150))
        return it

    def ev_rename(self, d, cancelled=False, future=False):
        ts = [t for t in range(len(TEAM_NAMES)) if self.renamed.get(t, 0) == 0]
        if not ts or not self.rename_pool:
            return None
        t = self.R.choice(ts)
        new = self.rename_pool.pop()
        show = cancelled or future or self.R.random() < 0.85
        it = self.item("rename", {"tid": t, "old": self.names[t], "new": new}, d, show, cancelled)
        self.order([it], self.sign_for(d, show, 8 if cancelled else 4, 25))
        self.put(("name", t), new, it)
        if not cancelled:
            self.renamed[t] = 1
            if not future:
                self.log(d, "rename", {"tid": t, "old": it.data["old"], "new": new})
        return it

    def ev_owner(self, d, cancelled=False, future=False):
        R = self.R
        ss = [s for s in range(len(SERVICES)) if (d - self.last_owner[s]).days >= 150]
        if not ss:
            return None
        s = R.choice(ss)
        a = self.owner[s]
        tg = [t for t in range(len(TEAM_NAMES)) if t != a]
        same = [t for t in tg if self.W.team_dept[t] == self.W.team_dept[a]]
        b = R.choice(same if R.random() < 0.7 else tg)
        show = cancelled or future or R.random() < 0.85
        it = self.item("owner", {"sid": s, "a": a, "b": b}, d, show, cancelled)
        self.order([it], self.sign_for(d, show, 8 if cancelled else 4, 25))
        self.put(("owner", s), b, it)
        if not cancelled:
            self.last_owner[s] = d
            if not future:
                self.log(d, "owner", {"sid": s, "a": a, "b": b})
        return it

    def ev_codename(self, d):
        ss = [s for s in range(len(SERVICES)) if s not in self.code_changed]
        s = self.R.choice(ss)
        new = self.code_pool.pop()
        show = self.R.random() < 0.85
        it = self.item("codename", {"sid": s, "old": self.codes[s], "new": new}, d, show)
        self.order([it], self.sign_for(d, show))
        self.put(("code", s), new, it)
        self.code_changed.add(s)
        self.log(d, "codename", {"sid": s, "old": it.data["old"], "new": new})
        return it

    def ev_hire(self, d):
        R = self.R
        tg = [t for t in range(len(TEAM_NAMES)) if self.size(t) < 8]
        t = R.choice(tg)
        p = self.new_person(d)
        c = self.W.team_city[t] if R.random() < 0.7 else R.choice(CITIES)
        ph = self.new_phone(c)
        it = self.item("hire", {"pid": p.pid, "b": t, "city": c, "phone": ph}, d, True)
        self.order([it], self.sign_for(d, True, 3, 14))
        for key, v in ((("emp", p.pid), True), (("team", p.pid), t), (("city", p.pid), c),
                       (("phone", p.pid), ph)):
            self.put(key, v, it)
        self.last_move[p.pid] = d
        self.log(d, "hire", {"pid": p.pid, "b": t, "city": c, "phone": ph})
        return it

    def ev_leave(self, d):
        cands = [p for p in sorted(self.emp) if self.team_of.get(p) is not None and not self.is_boss(p)
                 and (d - self.last_move.get(p, START)).days >= 60 and self.size(self.team_of[p]) >= 5]
        if not cands:
            return None
        p = self.R.choice(cands)
        t = self.team_of[p]
        it = self.item("leave", {"pid": p, "tid": t, "last": d - timedelta(days=1)}, d, True)
        self.order([it], self.sign_for(d, True, 3, 14))
        self.put(("emp", p), False, it)
        self.put(("team", p), None, it)
        self.log(d, "leave", {"pid": p, "tid": t})
        return it

    def phone_change(self, d, p, new, reason, announce=None):
        ann = announce or d - timedelta(days=self.R.randint(0, 8))
        it = Item("phone", {"pid": p, "old": self.phone[p], "new": new, "reason": reason}, d, ann, d)
        self.W.phone_news.append((ann, d, p, self.phone[p], new, reason))
        self.put(("phone", p), new, it)

    def ev_relocate(self, d):
        cands = [p for p in sorted(self.emp) if self.team_of.get(p) is not None
                 and p not in self.lead.values()]
        p = self.R.choice(cands)
        new = self.R.choice([c for c in CITIES if c != self.city[p]])
        show = self.R.random() < 0.85
        it = self.item("relocate", {"pid": p, "old": self.city[p], "new": new}, d, show)
        self.order([it], self.sign_for(d, show))
        self.put(("city", p), new, it)
        self.log(d, "relocate", {"pid": p, "old": it.data["old"], "new": new})
        self.phone_change(d, p, self.new_phone(new), "переезд в другой офис")
        return it

    def ev_phone(self, d, future=False):
        cands = [p for p in sorted(self.emp)]
        p = self.R.choice(cands)
        reason = self.R.choice(["переезд на другой этаж", "замена оборудования АТС на этаже",
                                "перенумерация кабинетов", "объединение рабочих зон команды"])
        ann = d - timedelta(days=self.R.randint(5, 20)) if future else None
        self.phone_change(d, p, self.new_phone(self.city[p]), reason, ann)

    # ---- сценарий ----
    def run(self) -> World:
        R, W = self.R, self.W
        self.initial()
        plan = {"transfer": 45, "lead": 12, "deputy": 10, "rename": 9, "owner": 16, "codename": 5,
                "hire": 16, "leave": 8, "relocate": 8, "phone": 12, "c_transfer": 2, "c_owner": 2,
                "c_rename": 1, "c_lead": 1}
        events = []
        for kind, n in plan.items():
            for _ in range(n):
                if R.random() < 0.3:
                    d = rdate(R, date(2024, 2, 5), date(2024, 12, 20))
                else:
                    d = rdate(R, date(2025, 1, 13), date(2026, 6, 22))
                events.append((d, kind))
        events.sort()
        cancelled = []
        for d, kind in events:
            if kind.startswith("c_"):
                it = getattr(self, "ev_" + kind[2:])(d, cancelled=True)
                if it is not None:
                    cancelled.append(it)
            else:
                getattr(self, "ev_" + kind)(d)
        # будущие приказы (вступают в силу после даты среза)
        for kind, d in (("rename", date(2026, 7, 13)), ("deputy", date(2026, 7, 1)),
                        ("owner", date(2026, 8, 3)), ("transfer", date(2026, 7, 6))):
            getattr(self, "ev_" + kind)(d, future=True)
        for d in (date(2026, 7, 1), date(2026, 7, 6), date(2026, 7, 13)):
            self.ev_phone(d, future=True)
        self.cancellations(cancelled)
        self.amendments()
        self.substitutions()
        self.incidents()
        self.rotation()
        self.number_orders()
        return W

    def cancellations(self, cancelled):
        for it in cancelled:
            s = it.sign + timedelta(days=self.R.randint(2, max(2, (it.eff - it.sign).days - 2)))
            self.order([self.item("cancel", {"ref": it}, s)], s, kind="cancel", ref=it)

    def amendments(self):
        R = self.R
        cands = [o for o in self.W.orders if o.kind == "main" and all(
            it.show_date and not it.cancelled and it.kind in
            ("transfer", "lead", "deputy", "rename", "owner", "codename", "relocate") for it in o.items)
            and date(2024, 3, 1) <= o.items[0].eff <= date(2026, 6, 20)]
        R.shuffle(cands)
        n = 0
        for o in cands:
            if n >= 12:
                break
            eff = o.items[0].eff
            k = (eff - o.sign).days
            if k >= 10 and R.random() < 0.4:
                orig = eff - timedelta(days=R.randint(5, k - 4))
            else:
                orig = eff + timedelta(days=R.randint(5, 25))
            lo = min(orig, eff)
            if (lo - o.sign).days < 3:
                continue
            asign = o.sign + timedelta(days=R.randint(1, (lo - o.sign).days - 1))
            for it in o.items:
                it.orig = orig
            self.order([self.item("amend", {"ref": o, "orig": orig, "new": eff}, asign)], asign,
                       kind="amend", ref=o)
            n += 1

    def changes(self, key, a: date, b: date) -> bool:
        for e in self.W.hist.get(key, ()):
            if e.item is not None and not e.item.cancelled and a <= e.item.eff <= b:
                return True
        return False

    def substitutions(self):
        R, W = self.R, self.W
        made = 0
        tries = 0
        flags = ["cancel"] + ["amend"] * 3 + [""] * 17
        while made < len(flags) and tries < 5000:
            tries += 1
            t = R.randrange(len(TEAM_NAMES))
            start = rdate(R, date(2024, 3, 4), date(2026, 6, 8))
            end = start + timedelta(days=R.randint(6, 20))
            flag = flags[made]
            orig_end = end
            if flag == "amend":
                orig_end = end + timedelta(days=R.choice([-1, 1]) * R.randint(3, 7))
            a, b = start - timedelta(days=5), max(end, orig_end) + timedelta(days=5)
            if b > date(2026, 6, 28) or self.changes(("lead", t), a, b):
                continue
            lead = W.val(("lead", t), start)
            if any(s.tid == t and s.start <= b + timedelta(days=10) and a <= s.end + timedelta(days=10)
                   for s in W.substs):
                continue
            dep = W.val(("dep", t), start)
            cands = [p for p in W.members(t, start) if p != lead]
            ok = [p for p in cands if not self.changes(("team", p), a, b) and not self.changes(("emp", p), a, b)]
            if not ok:
                continue
            sub = dep if (dep in ok and not self.changes(("dep", t), a, b) and R.random() < 0.75) else R.choice(ok)
            sign = start - timedelta(days=R.randint(3, 14))
            reason = R.choice(["ежегодного отпуска", "ежегодного отпуска", "командировки", "учебного отпуска"])
            s = Subst(t, lead, sub, start, end, orig_end, sign, flag == "cancel", reason=reason)
            it = self.item("subst", {"s": s}, start, True, s.cancelled)
            s.item = it
            self.order([it], sign)
            if flag == "cancel":
                cs = sign + timedelta(days=R.randint(1, max(1, (start - sign).days - 1)))
                self.order([self.item("cancel", {"ref": it}, cs)], cs, kind="cancel", ref=it)
            elif flag == "amend":
                hi = min(end, orig_end)
                asg = rdate(R, start + timedelta(days=1), hi - timedelta(days=1))
                self.order([self.item("subst_amend", {"s": s}, asg)], asg, kind="amend", ref=it)
            W.substs.append(s)
            made += 1

    def incidents(self):
        R, W = self.R, self.W
        lo, hi = date(2025, 1, 8), date(2026, 6, 24)
        slots: list[tuple[datetime, int | None, str]] = []
        subs = [s for s in W.substs if not s.cancelled and s.start >= lo and s.end <= hi]
        R.shuffle(subs)
        for s in subs[:11]:
            d = rdate(R, s.start, s.end)
            own = W.owned(s.tid, d)
            if own:
                slots.append((datetime(d.year, d.month, d.day, R.randint(9, 20), R.randint(0, 59)),
                              R.choice(own), ""))
        oc = [e for k, es in W.hist.items() if k[0] == "owner" for e in es
              if e.item is not None and not e.item.cancelled and lo + timedelta(days=25) <= e.item.eff <= hi]
        R.shuffle(oc)
        for e in oc[:8]:
            d = e.item.eff + timedelta(days=R.choice([-1, 1]) * R.randint(1, 20))
            slots.append((datetime(d.year, d.month, d.day, R.randint(0, 23), R.randint(0, 59)), e.key[1], ""))
        for i in range(8):
            d = monday_of(rdate(R, lo + timedelta(days=7), hi))
            h = 9 if i < 4 else R.randint(1, 9)
            slots.append((datetime(d.year, d.month, d.day, h, R.randint(0, 59)), None,
                          "t+" if i < 2 else ("t-" if i == 2 else "")))
        while len(slots) < 44:
            d = rdate(R, lo, hi)
            h = R.choice([2, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 23])
            slots.append((datetime(d.year, d.month, d.day, h, R.randint(0, 59)), None, ""))
        slots.sort(key=lambda x: x[0])
        codes = sorted(R.sample(range(1100, 3990), len(slots)))
        svc_corr = set(R.sample([i for i, s in enumerate(slots) if not s[2]], 4))
        for i, (dt, svc, tflag) in enumerate(slots):
            d = dt.date()
            if svc is None:
                svc = R.randrange(len(SERVICES))
            t = W.val(("owner", svc), d)
            team = W.members(t, d)
            dt_rep, svc_rep, corr = dt, svc, ""
            if tflag == "t+":  # истинно 09:xx, в отчёте ошибочно 10:xx
                dt_rep, corr = dt + timedelta(hours=1), "time"
            elif tflag == "t-":
                dt = dt + timedelta(hours=1)
                dt_rep, corr = dt - timedelta(hours=1), "time"
            if i in svc_corr:
                others = [s for s in range(len(SERVICES)) if W.val(("owner", s), d) != t]
                svc_rep, corr = R.choice(others), "svc"
            author = R.choice(team)
            closer = R.choice([p for p in team if p != author] or team)
            outs = [p for p in W.members(R.randrange(len(TEAM_NAMES)), d) if p not in team]
            resp = R.sample(team, min(len(team), R.randint(1, 3))) + R.sample(outs, min(len(outs), R.randint(0, 2)))
            W.incidents.append(Incident(f"INC-{codes[i]}", dt, dt_rep, svc, svc_rep,
                                        R.choice(["P1", "P2", "P2", "P3", "P3", "P4"]), R.randint(18, 290),
                                        author, closer, resp, R.randrange(len(CAUSES)), corr))

    def rotation(self):
        R, W = self.R, self.W
        for t in range(len(TEAM_NAMES)):
            ptr = R.randrange(7)
            mon = ROT_START
            while mon <= date(2026, 6, 29):
                lead = W.val(("lead", t), mon)
                mem = [p for p in W.members(t, mon) if p != lead] or [lead]
                W.rota[(t, mon)] = mem[ptr % len(mem)]
                ptr += 1
                mon += timedelta(days=7)
        forced = []
        for inc in W.incidents[::3]:
            t = W.val(("owner", inc.svc), inc.dt.date())
            mon = monday_of(inc.dt.date())
            if inc.dt.weekday() == 0 and inc.dt.hour < 10:
                mon -= timedelta(days=7)
            forced.append((t, mon))
        keys = sorted(W.rota)
        forced += [keys[i] for i in R.sample(range(len(keys)), 40)]
        for t, mon in forced:
            if (t, mon) in W.swaps or (t, mon) not in W.rota:
                continue
            lead = W.val(("lead", t), mon)
            alt = [p for p in W.members(t, mon) if p not in (lead, W.rota[(t, mon)])]
            if alt:
                W.swaps[(t, mon)] = R.choice(alt)

    def number_orders(self):
        W = self.W
        W.orders = [o for _, o in sorted(enumerate(W.orders), key=lambda x: (x[1].sign, x[0]))]
        per_year: dict[int, int] = {}
        for o in W.orders:
            n = per_year.get(o.sign.year, 0) + self.R.randint(1, 3)
            per_year[o.sign.year] = n
            o.no = f"{n}-ОРГ"


# --------------------------------------------------------------------------
# Текстовые банки
# --------------------------------------------------------------------------

CAUSES = [
    ("исчерпался пул соединений к базе данных",
     "После увеличения числа реплик приложения суммарное число соединений превысило лимит PostgreSQL. "
     "Новые экземпляры не могли получить соединение и падали по таймауту проверки готовности, а "
     "балансировщик продолжал отправлять на них часть трафика."),
    ("истёк TLS-сертификат внутреннего балансировщика",
     "Автоматическое продление сертификата не сработало: задача продления падала с ошибкой прав доступа "
     "к хранилищу секретов уже три недели, но алерт на это событие не был настроен."),
    ("миграция схемы заблокировала крупную таблицу",
     "Миграция добавляла индекс без параметра CONCURRENTLY. Таблица оказалась заблокирована на запись "
     "почти на сорок минут, и все транзакции, которые в неё писали, выстроились в очередь."),
    ("новая версия библиотеки сериализации дала утечку памяти",
     "Обновление библиотеки сериализации попало в релиз вместе с десятком других изменений. Под нагрузкой "
     "память процессов росла линейно, и через два-три часа поды завершались с OOMKilled."),
    ("консьюмеры очереди не справились с всплеском событий",
     "Партнёр выгрузил архив статусов за полгода одной пачкой. Консьюмеры обрабатывали события в один поток "
     "на партицию, отставание росло, а повторные попытки только увеличивали нагрузку."),
    ("ошибка в конфигурации после релиза",
     "В релизе поменялось имя переменной окружения с адресом зависимости. В одном из двух кластеров "
     "конфигурация не была обновлена, и сервис обращался к несуществующему адресу."),
    ("переполнился диск на узлах хранилища",
     "Ротация журналов была отключена во время прошлого обслуживания и не включена обратно. Диски на трёх "
     "узлах заполнились, после чего запись новых данных стала невозможна."),
    ("лавина повторных запросов от клиентов",
     "Мобильные клиенты повторяли запросы без экспоненциальной задержки. Кратковременная ошибка зависимости "
     "превратилась в многократный рост входящего трафика, который сервис не выдержал."),
    ("деградация DNS в одном из ЦОД",
     "Резолверы в ЦОД «Юг» отвечали с задержкой до нескольких секунд. Клиенты сервиса не кэшировали "
     "результаты разрешения имён, и каждая операция упиралась в медленный DNS."),
    ("взаимные блокировки в базе после изменения порядка обновлений",
     "Два обработчика стали обновлять одни и те же строки в разном порядке. При высокой конкуренции это "
     "приводило к взаимным блокировкам, транзакции откатывались и повторялись."),
    ("ошибка в правиле балансировки трафика",
     "Новое правило маршрутизации отправляло весь трафик одного региона на единственный экземпляр, "
     "который находился в режиме обслуживания."),
    ("внешний провайдер ограничил число запросов",
     "Провайдер без предупреждения снизил лимит запросов для нашего ключа. Ответы с кодом 429 "
     "обрабатывались как фатальные ошибки, и задачи не ставились на повтор."),
]

ALERTS = ["HighErrorRate", "LatencyP99High", "SLOBurnRate", "ErrorBudgetFastBurn", "SyntheticCheckFailed"]
METRICS = ["доли ответов 5xx", "задержки p99", "отставания консьюмеров", "числа перезапусков подов",
           "времени ответа базы", "числа ошибок авторизации"]

TIMELINE_OPEN = [
    "сработал алерт {alert}; {p} [подтвердил|подтвердила] получение и [открыл|открыла] инцидент.",
    "{p} [заметил|заметила] на дашборде рост {metric} и [написал|написала] в канал #incidents.",
    "служба поддержки сообщила о жалобах клиентов; {p} [начал|начала] проверку и [открыл|открыла] инцидент.",
]
TIMELINE_MID = [
    "{p} [подключился|подключилась] к разбору и [начал|начала] смотреть логи приложения.",
    "проверили зависимость — сервис «{other}»: по метрикам всё штатно, версия не менялась.",
    "{p} [предложил|предложила] откатить последний релиз ({ver}).",
    "откат на версию {ver} выполнен, доля ошибок снизилась, но не до нормы.",
    "{p} [увеличил|увеличила] пул соединений с {n1} до {n2}.",
    "трафик переключён на резервный кластер в ЦОД «{dc}».",
    "{p} [перезапустил|перезапустила] зависшие обработчики очереди.",
    "{p} [написал|написала] статус для службы поддержки и партнёров.",
    "{p} [подтвердил|подтвердила], что на стороне сервиса «{other}» изменений не было.",
    "{p} [нашёл|нашла] в логах повторяющуюся ошибку и [передал|передала] трассировку коллегам.",
    "к разбору [подключился|подключилась] {p} и [взял|взяла] на себя коммуникацию.",
    "{p} [отключил|отключила] флаг новой функциональности, который включали накануне.",
    "очередь разобрана, задержка обработки вернулась к норме.",
    "метрики стабильны в течение 30 минут, наблюдение продолжается.",
]
IMPACTS = [
    "Около {n} % запросов к сервису завершались ошибкой в течение {m} минут.",
    "Задержка обработки выросла в среднем до {s} секунд; часть партнёров получила данные с опозданием.",
    "Примерно {k} клиентов не смогли завершить операцию с первой попытки.",
    "Потери данных нет: все события были обработаны повторно после восстановления.",
    "Служба поддержки получила {k} обращений, связанных с инцидентом.",
    "Внешние SLA нарушены не были, внутренний SLO за месяц израсходован на {n} %.",
]
ACTIONS = [
    "Добавить алерт на ошибки задачи продления сертификатов",
    "Покрыть миграции проверкой на блокирующие операции в CI",
    "Ограничить число соединений на экземпляр и вынести лимит в конфигурацию",
    "Добавить экспоненциальную задержку повторов в клиентскую библиотеку",
    "Провести нагрузочное тестирование с профилем пикового дня",
    "Обновить runbook: порядок переключения на резервный кластер",
    "Включить ротацию журналов и проверку заполнения дисков",
    "Разделить релиз зависимостей и прикладных изменений",
    "Настроить кэширование DNS на стороне клиентов",
    "Согласовать с провайдером уведомления об изменении лимитов",
    "Добавить дашборд по отставанию консьюмеров в дежурный набор",
    "Провести учения по сценарию отказа зависимости",
]
TITLES = ["Деградация сервиса", "Частичная недоступность сервиса", "Рост задержек сервиса",
          "Ошибки обработки запросов в сервисе", "Недоступность сервиса", "Задержка обработки событий в сервисе"]

ABOUT = [
    "[Пришёл|Пришла] в компанию из {prev}, где [занимался|занималась] {area}.",
    "В свободное время {hobby}.",
    "Любит задачи, в которых нужно разобраться в чужом коде и сделать его понятнее.",
    "Отвечает за онбординг новичков в команде: если вы только пришли — пишите.",
    "[Выступал|Выступала] на внутреннем митапе с докладом про {topic}.",
    "Ведёт внутренний курс по {topic} для стажёров.",
    "Считает, что хороший runbook экономит больше времени, чем хороший код.",
    "Если нужно быстро найти, кто отвечает за сервис, — спрашивайте в личке, подскажет.",
    "[Участвовал|Участвовала] в проекте «{project}».",
    "Не любит созвоны без повестки, любит короткие письма с конкретным вопросом.",
    "По пятницам обычно работает из дома; по срочным вопросам [доступен|доступна] в мессенджере.",
    "Коллекционирует {collect}.",
]
PREV = ["«ТрансЛогик»", "«Инфосистемы Урала»", "«Бета-Консалт»", "«Цифровой перрон»", "«Кедр Технолоджи»",
        "банка «Разум»", "«Невского процессинга»", "«Альтаир Софт»", "студии «Пиксельный двор»"]
AREAS = ["интеграциями с банками", "складской логистикой", "мобильной разработкой", "хранилищами данных",
         "автоматизацией тестирования", "эксплуатацией высоконагруженных систем", "аналитикой продаж"]
HOBBIES = ["бегает полумарафоны", "играет в шахматы по переписке", "ходит в походы по Карелии",
           "фотографирует городскую архитектуру", "учит японский", "играет на бас-гитаре в любительской группе",
           "выращивает томаты на даче", "занимается скалолазанием", "варит кофе на всю команду",
           "катается на сапборде", "собирает модели парусников", "поёт в хоре"]
TOPICS = ["наблюдаемость", "нагрузочное тестирование", "PostgreSQL", "Kafka", "Kubernetes для разработчиков",
          "проектирование API", "разбор инцидентов без поиска виноватых", "ClickHouse"]
COLLECT = ["виниловые пластинки", "значки с конференций", "старые карты метро", "настольные игры"]
SKILLS = ["Python", "Go", "Java", "Kotlin", "PostgreSQL", "Kafka", "Kubernetes", "ClickHouse", "Terraform",
          "gRPC", "Airflow", "Rust", "TypeScript", "React", "Grafana", "Prometheus", "Redis", "Spark"]
POSITIONS = ["инженер-разработчик", "ведущий инженер-разработчик", "старший инженер-разработчик",
             "инженер по надёжности", "системный аналитик", "инженер по тестированию", "аналитик данных"]

TEAM_DESC = [
    "Команда отвечает за {what}. Мы работаем короткими итерациями и стараемся выпускать изменения "
    "маленькими порциями, чтобы любой релиз можно было откатить за несколько минут.",
    "Основная задача команды — {what}. Дежурства распределены по графику, вопросы вне рабочего времени "
    "направляйте дежурному, а не руководителю команды.",
    "Наша зона ответственности — {what}. Раз в квартал проводим день техдолга и публикуем его итоги в разделе "
    "«Новости».",
    "Команда отвечает за {what}. С момента создания её зона "
    "ответственности несколько раз менялась — актуальный список сервисов приведён ниже.",
]
TEAM_FOCUS = {
    "Платформа": "инфраструктурные сервисы, которыми пользуются все остальные команды",
    "Платежи": "приём платежей, расчёты с партнёрами и всё, что связано с деньгами",
    "Логистика": "путь посылки от склада партнёра до двери клиента",
    "Данные": "витрины, отчёты и поиск по данным компании",
}
SVC_OPS = [
    "Перед релизом обязательно прогоняются контрактные тесты с потребителями. Релизы по пятницам после "
    "16:00 не выкатываются без согласования с дежурным.",
    "При росте ошибок первым делом проверьте дашборд зависимостей: чаще всего проблема приходит извне. "
    "Перезапуск всех экземпляров сразу запрещён — только по одному.",
    "Конфигурация хранится в репозитории и выкатывается вместе с релизом; ручные правки в кластере "
    "перезаписываются при следующем деплое.",
    "Резервный кластер находится в ЦОД «Юг», переключение описано в runbook и занимает около десяти минут.",
    "Секреты хранятся в хранилище секретов; доступ к ним выдаёт команда-владелец по заявке.",
    "Плановые работы согласуются в канале #changes не позднее чем за сутки.",
]
NOISE_ITEMS = [
    "Утвердить обновлённый регламент проведения разборов инцидентов (приложение 1).",
    "Провести инвентаризацию ноутбуков и мониторов до конца текущего месяца.",
    "Руководителям команд обеспечить ознакомление сотрудников с настоящим приказом.",
    "Утвердить график проведения учений по отказоустойчивости на следующий квартал.",
    "Обновить списки рассылок и каналов команд в соответствии с настоящим приказом.",
    "Службе поддержки инфраструктуры обновить права доступа к репозиториям в течение трёх рабочих дней.",
]
PURPOSES = [
    "В целях оптимизации распределения ответственности за сервисы",
    "В целях повышения эффективности работы дирекции «{dept}»",
    "В целях обеспечения непрерывности работы команд",
    "На основании служебной записки директора дирекции «{dept}»",
    "В связи с производственной необходимостью",
    "В целях приведения организационной структуры в соответствие с продуктовой стратегией",
]
NEWS_FLUFF = [
    ("Хакатон", "В последние выходные месяца прошёл внутренний хакатон. Победила команда, собравшая за "
     "два дня прототип голосового помощника для курьеров. Презентации выложены в разделе «Встречи»."),
    ("Турнир по настольному теннису", "Открыта регистрация на весенний турнир. Играем по средам в "
     "переговорной «Волга» московского офиса; для остальных офисов — трансляция и свой зачёт."),
    ("Обновление VPN-клиента", "До конца месяца всем сотрудникам нужно обновить VPN-клиент. Старая версия "
     "перестанет подключаться после окончания срока действия сертификата."),
    ("Лекция о безопасности", "Команда информационной безопасности проведёт открытую лекцию о фишинге. "
     "Запись будет доступна всем, но живое участие засчитывается как ежегодный инструктаж."),
    ("Субботник", "Петербургский офис приглашает на субботник во дворе здания. Перчатки и чай — от "
     "административной службы."),
    ("Библиотека", "В казанском офисе открылась библиотека: берите книги, оставляйте свои. Каталог "
     "ведётся в общей таблице."),
    ("Столовая", "Столовая московского офиса закрыта на ремонт на две недели; компенсация питания "
     "начисляется автоматически."),
    ("Опрос вовлечённости", "Стартовал ежегодный опрос вовлечённости. Он анонимный, результаты по "
     "дирекциям будут опубликованы в следующем выпуске новостей."),
    ("Новый стенд нагрузочного тестирования", "Платформенные команды запустили общий стенд для "
     "нагрузочного тестирования. Заявки на слоты — через форму в разделе «Сервисы»."),
]
SYNC_STATUS = [
    "закончили миграцию сервиса «{svc}» на новую версию PostgreSQL, простоев не было",
    "в сервисе «{svc}» выросла нагрузка примерно на {n} %, готовят масштабирование",
    "релиз {ver} сервиса «{svc}» перенесён на неделю из-за замечаний безопасности",
    "закрыли {n} задач техдолга по {svc_d}, осталось около десятка",
    "дописывают runbook для {svc_g} после последнего инцидента",
    "согласовали с соседями контракт нового API {svc_g}; выкатка — после нагрузочного теста",
    "работают над снижением задержек {svc_g}: p99 уже снизили на {n} %",
    "идёт найм: открыта вакансия, собеседования по вторникам и четвергам",
]


# --------------------------------------------------------------------------
# Рендеринг
# --------------------------------------------------------------------------

NAV = [("people/index.html", "Люди"), ("teams/index.html", "Команды"), ("services/index.html", "Сервисы"),
       ("orders/index.html", "Приказы"), ("incidents/index.html", "Инциденты"),
       ("oncall/index.html", "Дежурства"), ("offices/index.html", "Офисы"), ("glossary.html", "Глоссарий"),
       ("meetings/index.html", "Встречи"), ("news/index.html", "Новости")]


def page(title: str, body: str, depth: int, updated: date | None = None, deprecated: str = "") -> str:
    up = "../" * depth
    nav = " | ".join(f'<a href="{up}{h}">{t}</a>' for h, t in NAV)
    banner = ""
    if deprecated:
        banner = (f'<div class="deprecated"><strong>Устарело.</strong> Страница больше не поддерживается, '
                  f'информация на ней может быть неактуальной. {deprecated}</div>\n')
    foot = f"Страница обновлена: {dd(updated)}" if updated else "Раздел обновляется автоматически"
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{html.escape(title)} — Интранет «Северный Вектор»</title>\n"
        f'<link rel="stylesheet" href="{up}static/site.css">\n</head>\n<body>\n'
        f'<header class="top"><a class="logo" href="{up}index.html">Северный Вектор · интранет</a>\n'
        f"<nav>{nav}</nav>\n"
        f'<form class="search" action="{up}search.html"><input name="q" placeholder="Поиск по интранету">'
        "</form></header>\n"
        f"{banner}<main>\n{body}\n</main>\n"
        f'<footer><p>{foot}</p><p>Вопросы по содержанию страницы — в канал #intranet. '
        "Внутренняя информация, не для передачи третьим лицам.</p></footer>\n</body>\n</html>\n"
    )


class Render:
    def __init__(self, W: World, R):
        self.W, self.R = W, R
        cnt: dict[str, int] = {}
        cnt2: dict[str, int] = {}
        for p in W.people:
            cnt[p.short()] = cnt.get(p.short(), 0) + 1
            cnt2[p.io()] = cnt2.get(p.io(), 0) + 1
        self.amb_short = {k for k, v in cnt.items() if v > 1}
        self.amb_io = {k for k, v in cnt2.items() if v > 1}

    def ref(self, pid, case="n", styles=("full", "init", "fio2", "init2")) -> str:
        p = self.W.people[pid]
        ok = [s for s in styles if not (s == "short" and p.short() in self.amb_short)
              and not (s == "io" and p.io() in self.amb_io)]
        if not ok:
            ok = ["full"]
        return getattr(p, self.R.choice(ok))(case)

    def link(self, pid, case="n", depth=1, styles=("full", "init", "fio2")) -> str:
        return f'<a href="{"../" * depth}people/{self.W.people[pid].slug}.html">{self.ref(pid, case, styles)}</a>'

    def tn(self, tid, d, case="n") -> str:
        return q(self.W.tname(tid, d), case)

    def sv(self, sid, d, case="n", official_ok=True) -> str:
        if official_ok and self.R.random() < 0.35:
            return f"«{SERVICES[sid][0]}»"
        return q(self.W.code(sid, d), case)

    def ds(self, d: date, cap=False) -> str:
        s = self.R.choice(["с " + dd(d), "с " + dlong(d), "с " + dlong2(d)])
        return s[0].upper() + s[1:] if cap else s

    # ---- пункты приказов ----
    def item_text(self, it: Item) -> str:
        R, W, s = self.R, self.W, it.sign
        d = it.orig
        D = (" " + self.ds(d)) if it.show_date else ""
        DC = (self.ds(d, cap=True) + " ") if it.show_date else ""
        x = it.data
        k = it.kind
        if k == "transfer":
            p = W.people[x["pid"]]
            A, B = self.tn(x["a"], s), self.tn(x["b"], s)
            v = [
                f"Перевести {p.full('a')} из команды {A} в команду {B}{D}.",
                f"{p.full('a')} перевести в команду {B} (из команды {A}){D}.",
                f"Включить в состав команды {B} {p.full('a')}, исключив {p.him()} из состава команды {A}{D}.",
                f"{DC}считать {p.full('a')} сотрудником команды {B}; в команде {A} {p.him('n')} более не числится.",
                f"{p.full('d')}{D} продолжить работу в составе команды {B} (ранее — команда {A}).",
                f"Закрепить {p.full('a')} за командой {B}{D} в связи с переводом из команды {A}.",
            ]
        elif k == "lead":
            p, o, T = W.people[x["pid"]], W.people[x["old"]], self.tn(x["tid"], s)
            v = [
                f"Назначить {p.full('a')} руководителем команды {T}{D}.",
                f"{DC}руководителем команды {T} назначается {p.full()}.",
                f"Возложить на {p.full('a')} руководство командой {T}{D}, освободив от этих обязанностей "
                f"{o.full('a')}.",
                f"{o.full('a')} освободить от обязанностей руководителя команды {T}{D}; руководителем команды "
                f"назначить {p.full('a')}.",
                f"Утвердить {p.full('a')} в должности руководителя команды {T}{D}.",
            ]
        elif k == "deputy":
            p, o, T = W.people[x["pid"]], W.people[x["old"]], self.tn(x["tid"], s)
            v = [
                f"Назначить {p.full('a')} заместителем руководителя команды {T}{D}.",
                f"{DC}обязанности заместителя руководителя команды {T} возложить на {p.full('a')} "
                f"(вместо {o.full('g')}).",
                f"Заместителем руководителя команды {T}{D} считать {p.full('a')}.",
                f"Утвердить {p.full('a')} заместителем руководителя команды {T}{D}.",
                f"{DC}{p.full('a')} назначить заместителем руководителя команды {T}.",
            ]
        elif k == "rename":
            old, new = x["old"], x["new"]
            v = [
                f"Переименовать команду {q(old)} в {q(new, 'a')}{D}.",
                f"{DC}команду {q(old)} именовать {q(new, 'i')}.",
                f"Изменить наименование команды {q(old)} на {q(new, 'a')}{D}; состав команды и закреплённые "
                f"за ней сервисы сохранить.",
                f"Команде {q(old)} присвоить новое наименование — {q(new)}{(' — ' + self.ds(d)) if it.show_date else ''}.",
                f"В связи с изменением профиля работ команду {q(old)}{D} переименовать в {q(new, 'a')}.",
            ]
        elif k == "owner":
            sid = x["sid"]
            off, code = SERVICES[sid][0], W.code(sid, s)
            A, B = self.tn(x["a"], s), self.tn(x["b"], s)
            v = [
                f"Передать сервис «{off}» (кодовое имя «{code}») из ведения команды {A} в ведение команды {B}{D}.",
                f"{DC}ответственной за сервис «{code}» назначить команду {B} вместо команды {A}.",
                f"Закрепить сервис «{off}» за командой {B}{D}; команде {A} передать дела и документацию.",
                f"Сервис «{code}»{D} переходит в зону ответственности команды {B} (ранее — {A}).",
                f"Владельцем сервиса «{off}»{D} определить команду {B}.",
            ]
        elif k == "codename":
            off, old, new = SERVICES[x["sid"]][0], x["old"], x["new"]
            v = [
                f"Присвоить сервису «{off}» новое кодовое имя «{new}» (прежнее — «{old}»){D}.",
                f"{DC}сервис «{old}» во внутренних документах именовать «{new}».",
                f"Изменить кодовое имя сервиса «{off}» с «{old}» на «{new}»{D}.",
                f"Кодовое имя «{old}»{D} вывести из употребления; сервис «{off}» далее обозначать как «{new}».",
            ]
        elif k == "hire":
            p, T, c = W.people[x["pid"]], self.tn(x["b"], s), x["city"]
            v = [
                f"Принять {p.full('a')} на работу в команду {T} (офис в {CITY_P[c]}); первый рабочий день — "
                f"{dany(R, it.eff)}.",
                f"Зачислить {p.full('a')} в состав команды {T} с {dd(it.eff)}, место работы — {cadj(c, 'n')} офис.",
                f"Оформить приём {p.full('g')} в команду {T} с {dlong(it.eff)}; место работы — офис в {CITY_P[c]}.",
                f"С {dd(it.eff)} принять {p.full('a')} в штат и включить в команду {T} ({cadj(c, 'n')} офис).",
            ]
        elif k == "leave":
            p, T, last = W.people[x["pid"]], self.tn(x["tid"], s), x["last"]
            v = [
                f"Расторгнуть трудовой договор с {p.full('i')} по инициативе работника; последний рабочий "
                f"день — {dany(R, last)}.",
                f"Уволить {p.full('a')} по собственному желанию; последний рабочий день — {dd(last)}.",
                f"{p.full('a')} исключить из состава команды {T} в связи с увольнением (последний рабочий день "
                f"{dd(last)}).",
                f"Прекратить трудовые отношения с {p.full('i')} по соглашению сторон; последним рабочим днём "
                f"считать {dlong(last)}.",
            ]
        elif k == "relocate":
            p, c = W.people[x["pid"]], x["new"]
            v = [
                f"Перевести {p.full('a')} на работу в {cadj(c, 'a')} офис{D}; командная принадлежность сохраняется.",
                f"{DC}местом работы {p.full('g')} считать офис в {CITY_P[c]}.",
                f"Изменить место работы {p.full('g')}{D}: офис в {CITY_P[c]}.",
                f"В связи с переездом закрепить за {p.full('i')} рабочее место в {cadj(c, 'p')} офисе{D}.",
                f"{p.full('d')}{D} приступить к работе в офисе в {CITY_P[c]}.",
            ]
        elif k == "subst":
            sb = x["s"]
            L, S, T = W.people[sb.lead], W.people[sb.sub], self.tn(sb.tid, s)
            a, b = dany(R, sb.start), dany(R, sb.orig_end)
            v = [
                f"На период {sb.reason} руководителя команды {T} {L.full('g')} с {a} по {b} включительно "
                f"исполнение обязанностей руководителя возложить на {S.full('a')}.",
                f"Возложить временное исполнение обязанностей руководителя команды {T} на {S.full('a')} "
                f"с {a} по {b} (на время {sb.reason} {L.init('g')}).",
                f"В период с {a} по {b} обязанности руководителя команды {T} исполняет {S.full()} в связи "
                f"с отсутствием {L.full('g')} (период {sb.reason}).",
                f"{S.full('d')} временно, с {a} по {b}, исполнять обязанности руководителя команды {T} на время "
                f"{sb.reason} {L.full('g')}.",
            ]
        elif k == "amend":
            o, orig, new = x["ref"], x["orig"], x["new"]
            ref = f"приказ № {o.no} от {dd(o.sign)}"
            refg = f"приказа № {o.no} от {dd(o.sign)}"
            v = [
                f"Внести изменение в {ref}: дату вступления в силу «{dd(orig)}» заменить датой «{dd(new)}».",
                f"Перенести срок вступления в силу {refg} на {dlong(new)}.",
                f"В связи с производственной необходимостью изменения, предусмотренные {'приказом № ' + o.no + ' от ' + dd(o.sign)}, "
                f"ввести в действие с {dd(new)} (вместо {dd(orig)}).",
                f"Датой вступления в силу {refg} считать {dd(new)}.",
            ]
        elif k == "subst_amend":
            sb = x["s"]
            o = sb.item.order
            refg = f"приказа № {o.no} от {dd(o.sign)}"
            if sb.end > sb.orig_end:
                v = [f"Срок временного исполнения обязанностей руководителя команды {self.tn(sb.tid, s)}, "
                     f"установленный {'приказом № ' + o.no + ' от ' + dd(o.sign)}, продлить по {dd(sb.end)} включительно.",
                     f"Продлить по {dlong(sb.end)} включительно срок исполнения обязанностей, предусмотренный {refg}."]
            else:
                v = [f"Срок временного исполнения обязанностей руководителя команды {self.tn(sb.tid, s)}, "
                     f"установленный {'приказом № ' + o.no + ' от ' + dd(o.sign)}, сократить: последний день "
                     f"исполнения обязанностей — {dd(sb.end)}.",
                     f"В связи с досрочным выходом из отпуска {W.people[sb.lead].full('g')} считать последним днём "
                     f"исполнения обязанностей по {refg} {dlong(sb.end)}."]
        elif k == "cancel":
            ref_it: Item = x["ref"]
            o = ref_it.order
            whole = len(o.items) == 1
            if whole:
                v = [f"Приказ № {o.no} от {dd(o.sign)} отменить до вступления в силу; предусмотренные им "
                     f"изменения не производить.",
                     f"Отменить приказ № {o.no} от {dd(o.sign)}.",
                     f"Признать приказ № {o.no} от {dd(o.sign)} не подлежащим исполнению."]
            else:
                v = [f"Пункт {ref_it.idx} приказа № {o.no} от {dd(o.sign)} отменить."]
        else:
            raise ValueError(k)
        text = re.sub(r"\.\.+", ".", R.choice(v))
        return text[0].upper() + text[1:]

    # ---- приказы ----
    SUBJ = {"transfer": "О переводе сотрудников", "lead": "О назначении руководителя команды",
            "deputy": "О назначении заместителя руководителя команды",
            "rename": "Об изменении наименования команды", "owner": "О передаче сервиса",
            "codename": "Об изменении кодового имени сервиса", "hire": "О приёме на работу",
            "leave": "О прекращении трудового договора", "relocate": "Об изменении места работы",
            "subst": "О временном исполнении обязанностей"}

    def order_html(self, o: Order) -> str:
        R, W = self.R, self.W
        ceo = next(p for p in W.people if p.role == "ceo")
        hr = next(p for p in W.people if p.role == "hr")
        if o.kind in ("amend", "cancel"):
            ro = o.items[0].data["ref"] if o.items[0].kind != "subst_amend" else o.items[0].data["s"].item.order
            if isinstance(ro, Item):
                ro = ro.order
            subj = ("О внесении изменений в приказ" if o.kind == "amend" else "Об отмене приказа") + \
                f" № {ro.no} от {dd(ro.sign)}"
        else:
            subj = self.SUBJ[o.items[0].kind]
        dept = DEPTS[R.randrange(4)]
        for it in o.items:
            t = it.data.get("tid", it.data.get("b"))
            if it.kind == "codename":
                dept = SERVICES[it.data["sid"]][1]
                break
            if it.kind == "subst":
                t = it.data["s"].tid
            if isinstance(t, int):
                dept = W.team_dept[t]
                break
        items = [self.item_text(it) for it in o.items]
        if o.kind == "main" and R.random() < 0.25:
            items.append(R.choice(NOISE_ITEMS))
        purpose = R.choice(PURPOSES).format(dept=dept)
        lis = "\n".join(f"<li>{t}</li>" for t in items)
        close = R.choice([f"Контроль за исполнением настоящего приказа возложить на директора по персоналу "
                          f"{hr.init('a')}", "Контроль за исполнением приказа оставляю за собой."])
        return (f'<article class="order" id="order-{o.sign.year}-{o.no}">\n'
                f"<h2>Приказ № {o.no} от {dd(o.sign)}</h2>\n<p class=\"subj\"><em>{subj}</em></p>\n"
                f"<p>{purpose} ПРИКАЗЫВАЮ:</p>\n<ol>\n{lis}\n</ol>\n<p>{close}</p>\n"
                f'<p class="sign">Генеральный директор {ceo.init2()}</p>\n</article>')

    def orders_pages(self) -> dict[str, str]:
        out = {}
        groups: dict[tuple, list] = {}
        for o in self.W.orders:
            groups.setdefault((o.sign.year, (o.sign.month - 1) // 3 + 1), []).append(o)
        rows = []
        for (y, qn), os_ in sorted(groups.items()):
            rn = ["I", "II", "III", "IV"][qn - 1]
            body = (f"<h1>Приказы по организационным вопросам: {rn} квартал {y} г.</h1>\n"
                    "<p>Приказы публикуются в порядке подписания. Дата вступления в силу указывается в тексте "
                    "пункта; изменения и отмены оформляются отдельными приказами.</p>\n"
                    + "\n".join(self.order_html(o) for o in os_))
            fn = f"{y}-q{qn}.html"
            out[f"orders/{fn}"] = page(f"Приказы, {rn} кв. {y}", body, 1, os_[-1].sign)
            rows.append(f'<li><a href="{fn}">{rn} квартал {y} г.</a> — приказов: {len(os_)}</li>')
        out["orders/index.html"] = page("Приказы", "<h1>Приказы</h1>\n<ul>\n" + "\n".join(rows) + "\n</ul>", 1)
        return out

    # ---- профили ----
    def exp_lines(self, pid, U) -> list[str]:
        W, p = self.W, self.W.people[pid]
        out = []
        for eff, kind, x in W.log:
            if eff > U or x.get("pid") != pid:
                continue
            if kind == "transfer":
                out.append(f"{dd(eff)} — {gfill('[перешёл|перешла]', p)} из команды {self.tn(x['a'], eff - timedelta(days=1))} "
                           f"в команду {self.tn(x['b'], eff)}")
            elif kind == "hire":
                out.append(f"{dd(eff)} — {gfill('[принят|принята]', p)} в команду {self.tn(x['b'], eff)}")
            elif kind == "lead":
                out.append(f"{dd(eff)} — {gfill('[назначен|назначена]', p)} руководителем команды {self.tn(x['tid'], eff)}")
            elif kind == "deputy":
                out.append(f"{dd(eff)} — {gfill('[назначен|назначена]', p)} заместителем руководителя команды "
                           f"{self.tn(x['tid'], eff)}")
            elif kind == "relocate":
                out.append(f"{dd(eff)} — переезд в офис в {CITY_P[x['new']]}")
        for s in W.substs:
            if s.sub == pid and not s.cancelled and s.end < U and self.R.random() < 0.5:
                out.append(f"{dd(s.start)}–{dd(s.end)} — {gfill('[исполнял|исполняла]', p)} обязанности руководителя "
                           f"команды {self.tn(s.tid, s.start)}")
        return out

    def profile(self, pid) -> tuple[str, str]:
        R, W = self.R, self.W
        p = W.people[pid]
        left = next((e.item.eff for e in W.hist[("emp", pid)] if e.item is not None and not e.item.cancelled
                     and e.value is False and e.item.eff <= SNAPSHOT), None)
        if left:
            U = left - timedelta(days=1)
        else:
            lo = max(p.hired, date(2025, 3, 1))
            U = rdate(R, lo, SNAPSHOT) if R.random() < 0.55 else rdate(R, max(lo, date(2026, 3, 1)), SNAPSHOT)
        t = W.val(("team", pid), U)
        city, phone = W.val(("city", pid), U), W.val(("phone", pid), U)
        role = "—"
        pos = R.choice(POSITIONS)
        if p.role == "ceo":
            pos = "генеральный директор"
        elif p.role == "hr":
            pos = "директор по персоналу"
        elif p.role.startswith("dir:"):
            pos = f"директор дирекции «{p.role[4:]}»"
        if t is not None:
            if W.val(("lead", t), U) == pid:
                role, pos = "руководитель команды", "руководитель команды"
            elif W.val(("dep", t), U) == pid:
                role = "заместитель руководителя"
            else:
                role = "участник команды"
        team_cell = (f'<a href="../teams/team-{t:02d}.html">{self.tn(t, U)}</a> (дирекция «{W.team_dept[t]}»)'
                     if t is not None else "—")
        about = []
        for tpl in R.sample(ABOUT, 3):
            about.append(gfill(tpl, p).format(prev=R.choice(PREV), area=R.choice(AREAS), hobby=R.choice(HOBBIES),
                                              topic=R.choice(TOPICS), project=R.choice(PROJECTS)[0],
                                              collect=R.choice(COLLECT)))
        exp = self.exp_lines(pid, U)
        exp_html = "\n".join(f"<li>{x}</li>" for x in exp) or "<li>Изменений в карточке не было.</li>"
        banner = ""
        if left:
            banner = f'<p class="note"><strong>{gfill("[Сотрудник|Сотрудница]", p)} больше не работает в компании.</strong></p>\n'
        body = (
            f"<h1>{p.full()}</h1>\n{banner}<p class=\"position\">{pos[0].upper() + pos[1:]}</p>\n"
            f'<table class="card">\n<tr><th>Команда</th><td>{team_cell}</td></tr>\n'
            f"<tr><th>Роль в команде</th><td>{role}</td></tr>\n"
            f"<tr><th>Офис</th><td>{city}, {CITY_ADDR[city]}, кабинет {p.room}</td></tr>\n"
            f"<tr><th>Внутренний телефон</th><td>{phone}</td></tr>\n"
            f"<tr><th>Эл. почта</th><td>{p.email}</td></tr>\n"
            f"<tr><th>В компании с</th><td>{dd(p.hired)}</td></tr>\n</table>\n"
            f"<h2>О себе</h2>\n<p>{' '.join(about)}</p>\n"
            f"<h2>Навыки</h2>\n<p>{', '.join(R.sample(SKILLS, R.randint(3, 6)))}</p>\n"
            f"<h2>Опыт в компании</h2>\n<ul>\n{exp_html}\n</ul>\n"
            f"<p class=\"hint\">Карточка отражает данные на дату обновления страницы.</p>"
        )
        return f"people/{p.slug}.html", page(p.full(), body, 1, U)

    # ---- команды ----
    def team_history(self, tid, U) -> list[str]:
        W, out = self.W, []
        for eff, kind, x in W.log:
            if eff > U:
                continue
            prev = eff - timedelta(days=1)
            p = W.people[x["pid"]] if "pid" in x else None
            if kind == "rename" and x["tid"] == tid:
                out.append(f"{dd(eff)} — команда переименована: {q(x['old'])} → {q(x['new'])}")
            elif kind == "lead" and x["tid"] == tid:
                out.append(gfill(f"{dd(eff)} — руководителем команды [назначен|назначена] {p.init()}", p))
            elif kind == "deputy" and x["tid"] == tid:
                out.append(gfill(f"{dd(eff)} — заместителем руководителя [назначен|назначена] {p.init()}", p))
            elif kind == "transfer" and x["b"] == tid:
                out.append(gfill(f"{dd(eff)} — в команду [перешёл|перешла] {p.init()} (из команды "
                                 f"{self.tn(x['a'], prev)})", p))
            elif kind == "transfer" and x["a"] == tid:
                out.append(gfill(f"{dd(eff)} — {p.init()} [перешёл|перешла] в команду {self.tn(x['b'], eff)}", p))
            elif kind == "hire" and x["b"] == tid:
                out.append(gfill(f"{dd(eff)} — к команде [присоединился|присоединилась] {p.init()} "
                                 f"(новый сотрудник компании)", p))
            elif kind == "leave" and x["tid"] == tid:
                out.append(f"{dd(prev)} — последний рабочий день {p.init('g')} в компании")
            elif kind == "owner" and x["b"] == tid:
                out.append(f"{dd(eff)} — команде передан сервис «{W.code(x['sid'], eff)}» "
                           f"(от команды {self.tn(x['a'], eff)})")
            elif kind == "owner" and x["a"] == tid:
                out.append(f"{dd(eff)} — сервис «{W.code(x['sid'], eff)}» передан команде {self.tn(x['b'], eff)}")
        return out

    def team_page(self, tid, U, deprecated="") -> str:
        R, W = self.R, self.W
        name = W.tname(tid, U)
        lead, dep = W.val(("lead", tid), U), W.val(("dep", tid), U)
        rows = []
        for pid in sorted(W.members(tid, U), key=lambda x: (x != lead, x != dep, W.people[x].sur())):
            role = "руководитель" if pid == lead else "заместитель руководителя" if pid == dep else "участник"
            rows.append(f"<tr><td>{self.link(pid, styles=('full',))}</td><td>{role}</td>"
                        f"<td>{W.val(('city', pid), U)}</td></tr>")
        svcs = [f'<li><a href="../services/svc-{s + 1:02d}.html">«{W.code(s, U)}»</a> — {SERVICES[s][0]}</li>'
                for s in W.owned(tid, U)] or ["<li>Сервисов за командой не закреплено.</li>"]
        hist = self.team_history(tid, U)
        desc = R.choice(TEAM_DESC).format(what=TEAM_FOCUS[W.team_dept[tid]])
        body = (
            f"<h1>Команда {q(name)}</h1>\n<p>Дирекция «{W.team_dept[tid]}». Основная площадка — "
            f"{cadj(W.team_city[tid], 'n')} офис.</p>\n<p>{desc}</p>\n"
            f"<h2>Состав</h2>\n<table>\n<tr><th>Сотрудник</th><th>Роль</th><th>Офис</th></tr>\n"
            + "\n".join(rows) + "\n</table>\n"
            "<h2>Сервисы команды</h2>\n<ul>\n" + "\n".join(svcs) + "\n</ul>\n"
            f"<h2>Как с нами связаться</h2>\n<p>Канал #team-{translit(name)}; срочные вопросы — дежурному "
            f"команды по графику (раздел «Дежурства»).</p>\n"
            f"<h2>История команды</h2>\n<ul>\n" + "\n".join(f"<li>{h}</li>" for h in hist[-14:]) + "\n</ul>\n"
            + ("<p class=\"hint\">Показаны последние записи; полная история — в разделе «Приказы».</p>"
               if len(hist) > 14 else "")
        )
        return page(f"Команда «{name}»", body, 1, U, deprecated)

    # ---- сервисы ----
    def service_page(self, sid, U) -> str:
        R, W = self.R, self.W
        off, dept, what = SERVICES[sid]
        code = W.code(sid, U)
        old = [e for e in W.hist[("code", sid)] if e.item is not None and not e.item.cancelled and e.item.eff <= U]
        codeline = f"Кодовое имя: «{code}»"
        if old:
            codeline += f" (до {dd(old[-1].item.eff)} — «{old[-1].item.data['old']}»)"
        t = W.val(("owner", sid), U)
        deps = R.sample([s for s in range(len(SERVICES)) if s != sid], 3)
        dep_html = "\n".join(
            f"<li>«{W.code(s, U)}» ({SERVICES[s][0].lower()}) — {R.choice(['синхронные вызовы', 'события через шину', 'ежедневная выгрузка', 'чтение справочников'])}</li>"
            for s in deps)
        hist = []
        for eff, kind, x in W.log:
            if eff <= U and x.get("sid") == sid:
                if kind == "owner":
                    hist.append(f"{dd(eff)} — сервис передан от команды {self.tn(x['a'], eff)} команде "
                                f"{self.tn(x['b'], eff)}")
                elif kind == "codename":
                    hist.append(f"{dd(eff)} — кодовое имя изменено: «{x['old']}» → «{x['new']}»")
        body = (
            f"<h1>{off}</h1>\n<p>{codeline}</p>\n<table class=\"card\">\n"
            f'<tr><th>Команда-владелец</th><td><a href="../teams/team-{t:02d}.html">{self.tn(t, U)}</a></td></tr>\n'
            f"<tr><th>Дирекция</th><td>{dept}</td></tr>\n"
            f"<tr><th>Критичность</th><td>{R.choice(['A', 'A', 'B', 'C'])}</td></tr>\n"
            f"<tr><th>SLO доступности</th><td>{R.choice(['99,9', '99,95', '99,5', '99,99'])} %</td></tr>\n"
            f"<tr><th>Репозиторий</th><td>git.svector.local/{translit(off)}</td></tr>\n</table>\n"
            f"<h2>Назначение</h2>\n<p>Сервис {what}. {R.choice(SVC_OPS)}</p>\n"
            f"<h2>Зависимости</h2>\n<ul>\n{dep_html}\n</ul>\n"
            f"<h2>Эксплуатация</h2>\n<p>{' '.join(R.sample(SVC_OPS, 3))}</p>\n"
            f"<h2>История</h2>\n<ul>\n" + ("\n".join(f"<li>{h}</li>" for h in hist) or "<li>Без изменений.</li>")
            + "\n</ul>"
        )
        return page(off, body, 1, U)

    # ---- инциденты ----
    def incident_page(self, inc: Incident) -> str:
        R, W = self.R, self.W
        d = inc.dt.date()
        t0 = inc.dt_rep
        svc_name = self.sv(inc.svc_rep, d)
        title = f"{R.choice(TITLES)} {svc_name}"
        det = t0 + timedelta(minutes=R.randint(2, 15))
        fin = t0 + timedelta(minutes=inc.dur)

        def hm(x: datetime) -> str:
            return f"{x.hour:02d}:{x.minute:02d}"

        def who(pid):
            t = W.val(("team", pid), d)
            own = W.val(("owner", inc.svc), d)
            r = self.ref(pid, "n", ("init", "init2", "short"))
            return r if t == own or t is None else f"{r} (команда {self.tn(t, d)})"

        people = [inc.author] + [x for x in inc.responders if x not in (inc.author, inc.closer)]
        lines = []
        tt = det
        first = W.people[inc.author]
        lines.append((tt, gfill(R.choice(TIMELINE_OPEN), first).format(
            alert=R.choice(ALERTS), metric=R.choice(METRICS), p=who(inc.author))))
        mids = R.sample(TIMELINE_MID, R.randint(5, 8))
        mids.sort(key=lambda x: ("норм" in x or "стабильн" in x))
        step = max(3, (inc.dur - 20) // (len(mids) + 1))
        for i, tpl in enumerate(mids):
            tt = tt + timedelta(minutes=R.randint(2, step))
            pid = people[(i + 1) % len(people)]
            other = R.choice([s for s in range(len(SERVICES)) if s not in (inc.svc, inc.svc_rep)])
            lines.append((tt, gfill(tpl, W.people[pid]).format(
                p=who(pid), other=W.code(other, d), ver=f"{R.randint(1, 9)}.{R.randint(0, 40)}.{R.randint(0, 9)}",
                n1=R.choice([50, 80, 100]), n2=R.choice([150, 200, 250]), dc=R.choice(["Север", "Юг"]))))
        lines.append((max(fin, tt + timedelta(minutes=5)), gfill(
            f"{who(inc.closer)} [подтвердил|подтвердила] стабильность метрик и [закрыл|закрыла] инцидент.",
            W.people[inc.closer])))
        tl = "\n".join(f"<li><b>{hm(x)}</b> — {y}</li>" for x, y in lines)
        short, long_ = CAUSES[inc.cause]
        impact = " ".join(R.sample(IMPACTS, 2)).format(n=R.randint(2, 40), m=R.randint(5, inc.dur),
                                                       s=R.randint(3, 90), k=R.randint(40, 4000))
        acts = "\n".join(
            f"<li>{a} — отв. {self.ref(R.choice(people), 'n', ('init',))}, срок {dd(d + timedelta(days=R.randint(10, 45)))}</li>"
            for a in R.sample(ACTIONS, R.randint(2, 4)))
        corr = ""
        if inc.corr_note == "time":
            cd = d + timedelta(days=R.randint(1, 6))
            corr = (f"<h2>Исправления</h2>\n<p>{dd(cd)}: время начала инцидента в карточке указано неверно — "
                    f"{hm(inc.dt_rep)}. Верное время начала — {hm(inc.dt)} (ошибка при переводе времени из "
                    f"журнала мониторинга); на тот же час сдвинуты и отметки времени в хронологии.</p>")
        body = (
            f"<h1>{inc.code}: {title}</h1>\n<table class=\"card\">\n"
            f"<tr><th>Статус</th><td>закрыт</td></tr>\n<tr><th>Уровень</th><td>{inc.sev}</td></tr>\n"
            f"<tr><th>Начало инцидента</th><td>{dd(t0.date())} {hm(t0)} (МСК)</td></tr>\n"
            f"<tr><th>Обнаружен</th><td>{hm(det)}</td></tr>\n<tr><th>Восстановлен</th><td>{dd(fin.date())} {hm(fin)}</td></tr>\n"
            f"<tr><th>Затронутый сервис</th><td>{svc_name}</td></tr>\n"
            f"<tr><th>Автор отчёта</th><td>{self.ref(inc.author, 'n', ('init', 'full'))}</td></tr>\n"
            f"<tr><th>Инцидент закрыт</th><td>{self.ref(inc.closer, 'i', ('init', 'full'))}</td></tr>\n</table>\n"
            f"<h2>Кратко</h2>\n<p>{dlong(t0.date()).capitalize()} ({WEEKDAYS_N[t0.weekday()]}) в {hm(t0)} начались проблемы с сервисом {svc_name}: {short}. "
            f"Инцидент продолжался около {inc.dur} мин.</p>\n"
            f"<h2>Хронология</h2>\n<ul>\n{tl}\n</ul>\n<h2>Влияние</h2>\n<p>{impact}</p>\n"
            f"<h2>Причина</h2>\n<p>{long_}</p>\n"
            f"<h2>Что сработало и что нет</h2>\n<ul><li>{R.choice(['Алерт сработал вовремя', 'Алерт сработал с опозданием', 'Первыми о проблеме сообщили клиенты'])}.</li>"
            f"<li>{R.choice(['Runbook помог локализовать проблему', 'Runbook оказался устаревшим', 'Откат занял больше времени, чем ожидалось'])}.</li></ul>\n"
            f"<h2>Действия по итогам</h2>\n<ul>\n{acts}\n</ul>\n{corr}"
        )
        return page(f"{inc.code}", body, 1, d + timedelta(days=R.randint(1, 5)))

    # ---- дежурства ----
    PERIODS = [("2025-h1", "I полугодие 2025 г.", date(2024, 12, 30), date(2025, 6, 23)),
               ("2025-h2", "II полугодие 2025 г.", date(2025, 6, 30), date(2025, 12, 29)),
               ("2026-h1", "I полугодие 2026 г.", date(2026, 1, 5), date(2026, 6, 29))]

    def oncall_pages(self) -> dict[str, str]:
        R, W = self.R, self.W
        out, idx = {}, []
        reasons = ["обмен сменами", "по болезни", "в связи с отпуском", "по договорённости в команде",
                   "в связи с командировкой"]
        for dept in DEPTS:
            for slug, title, a, b in self.PERIODS:
                parts, swaps = [], []
                for t in [x for x in range(len(TEAM_NAMES)) if W.team_dept[x] == dept]:
                    nm = self.tn(t, a)
                    later = [e for e in W.hist[("name", t)] if e.item is not None and not e.item.cancelled
                             and a < e.item.eff <= b + timedelta(days=6)]
                    if later:
                        nm += "".join(f" (с {dd(e.item.eff)} — {q(e.value)})" for e in later)
                    rows = []
                    mon = a
                    while mon <= b:
                        cur = W.rota[(t, mon)]
                        nxt = W.rota.get((t, mon + timedelta(days=7)), cur)
                        rows.append(f"<tr><td>{dd(mon)} – {dd(mon + timedelta(days=6))}</td>"
                                    f"<td>{W.people[cur].init()}</td><td>{W.people[nxt].init()}</td></tr>")
                        if (t, mon) in W.swaps:
                            sw = W.swaps[(t, mon)]
                            swaps.append(f"<li>Команда {self.tn(t, mon)}, неделя {dshort(mon)}–"
                                         f"{dshort(mon + timedelta(days=6))}: вместо {W.people[cur].init('g')} "
                                         f"дежурит {W.people[sw].init()} ({R.choice(reasons)}).</li>")
                        mon += timedelta(days=7)
                    parts.append(f"<h2>Команда {nm}</h2>\n<table>\n<tr><th>Неделя (пн – вс)</th><th>Дежурный</th>"
                                 f"<th>Резерв</th></tr>\n" + "\n".join(rows) + "\n</table>")
                body = (f"<h1>График дежурств: дирекция «{dept}», {title}</h1>\n"
                        "<p>Дежурная неделя начинается в понедельник в 10:00 и заканчивается в 10:00 следующего "
                        "понедельника. Резервный дежурный подключается только по вызову основного. Замены "
                        "перечислены в конце страницы и имеют приоритет над таблицей.</p>\n"
                        + "\n".join(parts) + "\n<h2>Замены</h2>\n<ul>\n"
                        + ("\n".join(swaps) or "<li>Замен нет.</li>") + "\n</ul>")
                fn = f"{DEPT_SLUG[dept]}-{slug}.html"
                out[f"oncall/{fn}"] = page(f"Дежурства: {dept}, {title}", body, 1, min(b, SNAPSHOT))
                idx.append(f'<li><a href="{fn}">Дирекция «{dept}», {title}</a></li>')
        out["oncall/index.html"] = page("Дежурства", "<h1>Графики дежурств</h1>\n<ul>\n" + "\n".join(idx)
                                        + "\n</ul>", 1)
        return out

    # ---- встречи ----
    DISCUSS = [
        "Обсудили, почему алерт не сработал раньше; решили пересмотреть пороги.",
        "{p} [отметил|отметила], что похожая ситуация уже была в прошлом квартале.",
        "Отдельно обсудили коммуникацию с партнёрами: статус опубликовали с задержкой.",
        "Вопросов к хронологии нет, действия по итогам согласованы.",
        "Договорились провести учения по этому сценарию до конца квартала.",
        "{p} [попросил|попросила] добавить в отчёт графики до и после отката.",
        "Решили, что повторный разбор не нужен, если поручения будут закрыты в срок.",
    ]

    def meetings(self) -> dict[str, str]:
        R, W = self.R, self.W
        out, idx = {}, []
        dirs = {p.role[4:]: p.pid for p in W.people if p.role.startswith("dir:")}
        # разборы инцидентов
        dates, d = [], date(2025, 3, 5)
        while d < date(2026, 6, 20):
            dates.append(d)
            d += timedelta(days=R.randint(50, 68))
        dates.append(date(2026, 6, 29))
        done: set[str] = set()
        for md in dates:
            incs = [i for i in W.incidents if i.dt.date() < md and i.code not in done]
            if not incs:
                continue
            done.update(i.code for i in incs)
            att = sorted({i.author for i in incs} | {i.closer for i in incs} | set(R.sample(list(dirs.values()), 2)))
            parts, corrs = [], []
            for inc in incs:
                a = W.people[inc.author]
                idt = inc.dt.date()
                txt = [gfill(f"[Докладывал|Докладывала] {self.ref(inc.author, 'n', ('short', 'init2'))}.", a),
                       f"Причина: {CAUSES[inc.cause][0]}."]
                for tpl in R.sample(self.DISCUSS, 2):
                    pp = R.choice(att)
                    txt.append(gfill(tpl, W.people[pp]).format(p=self.ref(pp, "n", ("short", "init2"))))
                if inc.corr_note == "svc":
                    txt.append(gfill(f"{self.ref(inc.author, 'n', ('short', 'init2'))} [уточнил|уточнила], что в отчёте ошибочно указан другой "
                                     f"сервис, — см. раздел «Исправления к отчётам».", a))
                    rep = W.code(inc.svc_rep, idt)
                    corrs.append(f"<li>{inc.code}: в поле «Затронутый сервис» ошибочно указан «{rep}» "
                                 f"({SERVICES[inc.svc_rep][0].lower()}). Верно: сервис «{W.code(inc.svc, idt)}» "
                                 f"({SERVICES[inc.svc][0].lower()}).</li>")
                parts.append(f"<h2>{inc.code} от {dd(idt)}</h2>\n<p>{' '.join(txt)}</p>")
            body = (f"<h1>Разбор инцидентов — {dd(md)}</h1>\n<p>Присутствовали: "
                    + ", ".join(self.ref(p, "n", ("init2", "init")) for p in att) + ".</p>\n"
                    + "\n".join(parts)
                    + ("\n<h2>Исправления к отчётам</h2>\n<ul>\n" + "\n".join(corrs) + "\n</ul>" if corrs else "")
                    + "\n<h2>Поручения</h2>\n<p>Владельцам действий по итогам — обновить статусы в трекере до "
                    "следующего разбора.</p>")
            fn = f"{md.isoformat()}-razbor-intsidentov.html"
            out[f"meetings/{fn}"] = page(f"Разбор инцидентов {dd(md)}", body, 1, md + timedelta(days=1))
            idx.append((md, f'<li><a href="{fn}">{dd(md)} — разбор инцидентов</a></li>'))
        # синки дирекций
        for base in (date(2025, 2, 12), date(2025, 6, 18), date(2025, 10, 15), date(2026, 3, 11)):
            for dept in DEPTS:
                md = base + timedelta(days=R.randint(0, 6))
                teams = [t for t in range(len(TEAM_NAMES)) if W.team_dept[t] == dept]
                leads = [W.val(("lead", t), md) for t in teams]
                att = [dirs[dept]] + leads
                st = []
                for t, ld in zip(teams, leads, strict=True):
                    own = W.owned(t, md) or [R.randrange(len(SERVICES))]
                    lines = []
                    for tpl in R.sample(SYNC_STATUS, 2):
                        sv = R.choice(own)
                        c = W.code(sv, md)
                        lines.append(tpl.format(svc=c, svc_d=q(c, "d"), svc_g=q(c, "g"), n=R.randint(3, 40),
                                                ver=f"{R.randint(1, 9)}.{R.randint(0, 30)}"))
                    st.append(f"<p><b>{self.tn(t, md)}</b> ({self.ref(ld, 'n', ('io', 'short'))}): "
                              f"{'; '.join(lines)}.</p>")
                pers = []
                for it in W.items:
                    if it.kind not in ("transfer", "lead", "rename") or not (md < it.eff <= md + timedelta(days=60)):
                        continue
                    if it.sign > md + timedelta(days=10) or R.random() < 0.4:
                        continue
                    x = it.data
                    tt = x.get("b", x.get("tid"))
                    if W.team_dept[tt] != dept:
                        continue
                    if it.kind == "transfer":
                        pers.append(f"Готовится переход {self.ref(x['pid'], 'g', ('short', 'init2'))} в команду "
                                    f"{self.tn(x['b'], md)}.")
                    elif it.kind == "lead":
                        pers.append(f"Обсуждали смену руководителя команды {self.tn(x['tid'], md)}; кандидатура — "
                                    f"{self.ref(x['pid'], 'n', ('short', 'init2'))}.")
                    else:
                        pers.append(f"Команда {self.tn(x['tid'], md)} хочет сменить название на {q(x['new'], 'a')}.")
                for _ in range(R.randint(1, 2)):
                    sv, tt = R.randrange(len(SERVICES)), R.choice(teams)
                    pers.append(R.choice([
                        f"Предложение: передать сервис «{W.code(sv, md)}» команде {self.tn(tt, md)}. Решение отложено "
                        f"до квартального планирования.",
                        f"{self.ref(R.choice(leads), 'n', ('short',))} предлагает объединить дежурства команд "
                        f"{self.tn(tt, md)} и {self.tn(R.choice(teams), md)}; пока не поддержано.",
                        f"Рассматривается переименование команды {self.tn(tt, md)} в «Полюс»; вопрос вынесен на "
                        f"следующий синк.",
                    ]))
                for s in W.substs:
                    if s.tid in teams and not s.cancelled and md < s.start <= md + timedelta(days=45):
                        pers.append(f"{self.ref(s.lead, 'n', ('io', 'short'))} в {MONTHS_P[s.start.month - 1]} "
                                    f"будет в отъезде, на это время за старшего — {self.ref(s.sub, 'n', ('short',))} "
                                    f"(приказ готовится).")
                body = (f"<h1>Синк дирекции «{dept}» — {dd(md)}</h1>\n<p>Присутствовали: "
                        + ", ".join(self.ref(p, "n", ("init2", "short")) for p in att) + ".</p>\n"
                        "<h2>1. Статусы команд</h2>\n" + "\n".join(st)
                        + "\n<h2>2. Кадровые и организационные вопросы</h2>\n<ul>\n"
                        + "\n".join(f"<li>{x}</li>" for x in pers) + "\n</ul>\n"
                        "<h2>3. Разное</h2>\n<p>" + R.choice(NEWS_FLUFF)[1] + "</p>\n"
                        "<p class=\"hint\">Протокол синка не является распорядительным документом.</p>")
                fn = f"{md.isoformat()}-sink-{DEPT_SLUG[dept]}.html"
                out[f"meetings/{fn}"] = page(f"Синк дирекции «{dept}» {dd(md)}", body, 1, md + timedelta(days=1))
                idx.append((md, f'<li><a href="{fn}">{dd(md)} — синк дирекции «{dept}»</a></li>'))
        idx.sort()
        out["meetings/index.html"] = page("Встречи", "<h1>Протоколы встреч</h1>\n<ul>\n"
                                          + "\n".join(x for _, x in idx) + "\n</ul>", 1)
        return out

    # ---- новости ----
    def news(self) -> dict[str, str]:
        R, W = self.R, self.W
        out, idx = {}, []
        y, m = 2024, 1
        while (y, m) <= (2026, 6):
            items = []
            for eff, kind, x in W.log:
                if (eff.year, eff.month) != (y, m):
                    continue
                if kind == "hire":
                    p = W.people[x["pid"]]
                    items.append(("Добро пожаловать", gfill(
                        f"С {dlong(eff)} к команде {self.tn(x['b'], eff)} [присоединился|присоединилась] "
                        f"{p.fio2()}. Рабочее место — {cadj(x['city'], 'n')} офис, внутренний номер {x['phone']}. "
                        f"{R.choice(ABOUT[1:2]).format(hobby=R.choice(HOBBIES))}", p)))
                elif kind == "leave" and R.random() < 0.8:
                    p = W.people[x["pid"]]
                    items.append(("Прощаемся", gfill(
                        f"{p.fio2()} [завершил|завершила] работу в компании (последний рабочий день — "
                        f"{dd(eff - timedelta(days=1))}). Спасибо за годы в команде {self.tn(x['tid'], eff)}!", p)))
                elif kind == "relocate" and R.random() < 0.6:
                    p = W.people[x["pid"]]
                    items.append(("Переезды", gfill(f"{p.short()} [переехал|переехала] в {CITY_A[x['new']]} и теперь "
                                                    f"работает в {cadj(x['new'], 'p')} офисе.", p)))
            ph = [n for n in W.phone_news if (n[0].year, n[0].month) == (y, m)]
            if ph:
                rows = "\n".join(
                    f"<tr><td>{self.ref(pid, 'n', ('full', 'fio2'))}</td><td>{old}</td><td>{new}</td>"
                    f"<td>{dd(eff)}</td><td>{reason}</td></tr>" for ann, eff, pid, old, new, reason in sorted(ph))
                items.append(("Изменения внутренних номеров",
                              "Новые номера начинают работать с указанной даты; до неё звоните по старым.\n"
                              "<table><tr><th>Сотрудник</th><th>Был</th><th>Стал</th><th>С даты</th><th>Причина</th></tr>\n"
                              + rows + "\n</table>"))
            for title, text in R.sample(NEWS_FLUFF, R.randint(1, 2)):
                items.append((title, text))
            R.shuffle(items)
            body = (f"<h1>Новости компании: {MONTHS_N[m - 1]} {y}</h1>\n"
                    + "\n".join(f"<article><h2>{t}</h2>\n<p>{x}</p></article>" for t, x in items))
            fn = f"{y}-{m:02d}.html"
            last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
            out[f"news/{fn}"] = page(f"Новости: {MONTHS_N[m - 1]} {y}", body, 1, min(last, SNAPSHOT))
            idx.append(f'<li><a href="{fn}">{MONTHS_N[m - 1].capitalize()} {y}</a></li>')
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        out["news/index.html"] = page("Новости", "<h1>Новости</h1>\n<ul>\n" + "\n".join(reversed(idx))
                                      + "\n</ul>", 1)
        return out

    # ---- справочники ----
    def glossary(self) -> str:
        W, U = self.W, date(2026, 3, 16)
        rows = []
        for s in sorted(range(len(SERVICES)), key=lambda s: W.code(s, U)):
            rows.append(f"<tr><td>«{W.code(s, U)}»</td><td>сервис</td><td>{SERVICES[s][0]}</td></tr>")
            for e in W.hist[("code", s)]:
                if e.item is not None and not e.item.cancelled and e.item.eff <= U:
                    rows.append(f"<tr><td>«{e.item.data['old']}»</td><td>прежнее кодовое имя</td><td>до "
                                f"{dd(e.item.eff)}; сейчас сервис «{SERVICES[s][0]}» называется «{e.value}»</td></tr>")
        for nm, desc in PROJECTS:
            rows.append(f"<tr><td>«{nm}»</td><td>проект</td><td>{desc}</td></tr>")
        for t in range(len(TEAM_NAMES)):
            for e in W.hist[("name", t)]:
                if e.item is not None and not e.item.cancelled and e.item.eff <= U:
                    rows.append(f"<tr><td>«{e.item.data['old']}»</td><td>прежнее название команды</td>"
                                f"<td>с {dd(e.item.eff)} команда называется «{e.value}»</td></tr>")
        body = ("<h1>Глоссарий кодовых имён</h1>\n<p>Кодовые имена сервисов, проектов и прежние названия команд. "
                "Официальные имена сервисов не меняются; кодовые имена могут меняться приказом.</p>\n"
                "<table>\n<tr><th>Имя</th><th>Тип</th><th>Пояснение</th></tr>\n" + "\n".join(rows) + "\n</table>")
        return page("Глоссарий", body, 0, U)

    def offices(self) -> dict[str, str]:
        R, W = self.R, self.W
        out, idx = {}, []
        for c in CITIES:
            teams = [t for t in range(len(TEAM_NAMES)) if W.team_city[t] == c]
            tl = ", ".join(self.tn(t, SNAPSHOT) for t in teams) or "команд с основной площадкой здесь нет"
            body = (f"<h1>Офис: {c}</h1>\n<p>Адрес: {CITY_ADDR[c]}. Ресепшен: внутренний {CITY_PHONE[c] + 1}. "
                    f"Внутренние номера сотрудников этого офиса начинаются на {str(CITY_PHONE[c])[0]}.</p>\n"
                    f"<p>Основная площадка команд: {tl}.</p>\n<h2>Как добраться</h2>\n<p>"
                    + R.choice(["От метро 7 минут пешком, вход со двора.", "Пропуск для гостей заказывается накануне "
                                "через форму на этой странице.", "Парковка для сотрудников — по заявке в "
                                "административную службу."]) + "</p>\n<h2>Правила</h2>\n<p>"
                    + " ".join(R.sample([s for _, s in NEWS_FLUFF], 2)) + "</p>")
            fn = f"{CITY_SLUG[c]}.html"
            out[f"offices/{fn}"] = page(f"Офис: {c}", body, 1, date(2026, 5, 20))
            idx.append(f'<li><a href="{fn}">{c}</a></li>')
        out["offices/index.html"] = page("Офисы", "<h1>Офисы</h1>\n<ul>\n" + "\n".join(idx) + "\n</ul>", 1)
        return out

    def deprecated(self) -> dict[str, str]:
        W, out = self.W, {}
        d1 = date(2024, 6, 3)
        rows = [f"<tr><td>{p.full()}</td><td>{self.tn(W.val(('team', p.pid), d1), d1) if W.val(('team', p.pid), d1) is not None else '—'}</td>"
                f"<td>{W.val(('phone', p.pid), d1)}</td></tr>" for p in sorted(W.people, key=lambda p: p.full())
                if W.val(("emp", p.pid), d1)]
        out["people/phonebook-2024.html"] = page(
            "Телефонный справочник 2024", "<h1>Телефонный справочник (июнь 2024)</h1>\n<table>\n<tr><th>Сотрудник</th>"
            "<th>Команда</th><th>Внутренний</th></tr>\n" + "\n".join(rows) + "\n</table>", 1, d1,
            'Актуальные номера — в карточках сотрудников и в объявлениях раздела «Новости».')
        d2 = date(2024, 9, 2)
        rows = [f"<tr><td>{SERVICES[s][0]}</td><td>«{W.code(s, d2)}»</td><td>{self.tn(W.val(('owner', s), d2), d2)}</td></tr>"
                for s in range(len(SERVICES))]
        out["services/catalog-2024.html"] = page(
            "Каталог сервисов 2024", "<h1>Каталог сервисов (сентябрь 2024)</h1>\n<table>\n<tr><th>Сервис</th>"
            "<th>Кодовое имя</th><th>Владелец</th></tr>\n" + "\n".join(rows) + "\n</table>", 1, d2,
            'Актуальные данные — на страницах сервисов.')
        for t in range(len(TEAM_NAMES)):
            ren = [e for e in W.hist[("name", t)] if e.item is not None and not e.item.cancelled
                   and e.item.eff <= SNAPSHOT]
            if ren and self.R.random() < 0.7:
                e = ren[0]
                U = max(e.item.eff - timedelta(days=self.R.randint(30, 150)), date(2024, 1, 15))
                if e.item.eff <= U:
                    continue
                out[f"teams/old-{translit(e.item.data['old'])}.html"] = self.team_page(
                    t, U, f'Актуальная страница команды: <a href="team-{t:02d}.html">team-{t:02d}</a>.')
        return out

    # ---- сборка сайта ----
    def site(self) -> dict[str, str]:
        R, W = self.R, self.W
        files: dict[str, str] = {}
        files["static/site.css"] = ("body{font-family:sans-serif;margin:0}header.top{background:#123;color:#fff;"
                                    "padding:8px}header a{color:#fff}main{padding:16px;max-width:980px}"
                                    "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:3px 6px}"
                                    ".deprecated{background:#fdd;padding:8px}footer{color:#666;padding:16px}\n")
        people_rows = []
        for p in W.people:
            path, html_ = self.profile(p.pid)
            files[path] = html_
        for p in sorted(W.people, key=lambda p: p.full()):
            people_rows.append(f'<li><a href="{p.slug}.html">{p.full()}</a></li>')
        files["people/index.html"] = page("Люди", "<h1>Сотрудники</h1>\n<p>Карточки сотрудников в алфавитном "
                                          "порядке. Старый телефонный справочник: <a href=\"phonebook-2024.html\">"
                                          "июнь 2024</a>.</p>\n<ul>\n" + "\n".join(people_rows) + "\n</ul>", 1)
        tl = []
        for t in range(len(TEAM_NAMES)):
            U = rdate(R, date(2025, 9, 1), SNAPSHOT) if R.random() < 0.6 else rdate(R, date(2025, 3, 1), SNAPSHOT)
            files[f"teams/team-{t:02d}.html"] = self.team_page(t, U)
            tl.append(f'<li><a href="team-{t:02d}.html">{self.tn(t, SNAPSHOT)}</a> — дирекция «{W.team_dept[t]}»</li>')
        files.update(self.deprecated())
        arch = [f'<li><a href="{k[6:]}">{k[10:-5]}</a></li>' for k in sorted(files) if k.startswith("teams/old-")]
        files["teams/index.html"] = page("Команды", "<h1>Команды</h1>\n<ul>\n" + "\n".join(tl) + "\n</ul>\n"
                                         "<h2>Архив страниц</h2>\n<ul>\n" + "\n".join(arch) + "\n</ul>", 1, SNAPSHOT)
        sl = []
        for s in range(len(SERVICES)):
            U = rdate(R, date(2025, 6, 1), SNAPSHOT)
            files[f"services/svc-{s + 1:02d}.html"] = self.service_page(s, U)
            sl.append(f'<li><a href="svc-{s + 1:02d}.html">{SERVICES[s][0]}</a></li>')
        files["services/index.html"] = page("Сервисы", "<h1>Каталог сервисов</h1>\n<ul>\n" + "\n".join(sl)
                                            + "\n</ul>\n<p>Архив: <a href=\"catalog-2024.html\">каталог 2024 г.</a></p>", 1)
        il = []
        for inc in W.incidents:
            files[f"incidents/{inc.code}.html"] = self.incident_page(inc)
            il.append(f'<li><a href="{inc.code}.html">{inc.code}</a> — {dd(inc.dt_rep.date())}</li>')
        files["incidents/index.html"] = page("Инциденты", "<h1>Отчёты об инцидентах</h1>\n<ul>\n" + "\n".join(il)
                                             + "\n</ul>", 1)
        files.update(self.orders_pages())
        files.update(self.oncall_pages())
        files.update(self.meetings())
        files.update(self.news())
        files.update(self.offices())
        files["glossary.html"] = self.glossary()
        files["index.html"] = page("Главная", (
            "<h1>Интранет ООО «Северный Вектор»</h1>\n<p>Здесь собраны карточки сотрудников, страницы команд и "
            "сервисов, приказы, отчёты об инцидентах, графики дежурств, протоколы встреч и новости. Справочные "
            "страницы обновляются владельцами вручную, поэтому у каждой указана дата обновления.</p>\n<ul>\n"
            + "\n".join(f'<li><a href="{h}">{t}</a></li>' for h, t in NAV) + "\n</ul>"), 0, SNAPSHOT)
        return {"intranet/" + k: v for k, v in files.items()}


# --------------------------------------------------------------------------
# Вопросы
# --------------------------------------------------------------------------


def val_entry_date(W: World, key, d: date, mode: Mode):
    """Дата вступления в силу записи, действующей на дату d (None — исходное состояние)."""
    best = None
    for e in W.hist.get(key, ()):
        if e.item is not None and e.item.cancelled and mode.cancel:
            continue
        ed = W._d(e, mode)
        if ed <= d and (best is None or (ed, e.seq) >= best[0]):
            best = ((ed, e.seq), e)
    if best is None or best[1].item is None:
        return None
    return best[0][0]


@dataclass
class Q:
    tpl: str
    text: str
    kind: str  # fio | int | date | name
    fn: object
    key: tuple = ()

    def ans(self, mode: Mode) -> str:
        try:
            v = self.fn(mode)
        except (KeyError, TypeError, IndexError, AttributeError):
            return ""
        return "" if v is None else str(v)


def candidates(W: World, R) -> list[Q]:
    out: list[Q] = []
    S = SNAPSHOT

    def owner_at(inc, m):
        dt = W.inc_dt(inc, m)
        d = W.at(dt.date(), m)
        return W.val(("owner", W.inc_svc(inc, m)), d, m), d, dt

    for inc in W.incidents:
        c = inc.code

        def f1(m, inc=inc):
            t, d, _ = owner_at(inc, m)
            return W.fio(W.lead_actual(t, d, m))
        out.append(Q("T1", f"Кто руководил командой, которой принадлежал сервис, пострадавший в инциденте {c}, "
                     f"на момент начала этого инцидента?", "fio", f1, (c,)))

        def f3(m, inc=inc):
            dt = W.inc_dt(inc, m)
            t = W.val(("owner", inc.svc), W.at(dt.date(), m), m)
            return W.fio(W.oncall(t, dt, m))
        out.append(Q("T3", f"Кто был дежурным команды, которой принадлежал сервис «{SERVICES[inc.svc][0]}», в момент "
                     f"начала инцидента {c} (по графику дежурств с учётом замен)?", "fio", f3, (c,)))

        def f4(m, inc=inc):
            t = W.val(("team", inc.closer), S, m)
            return None if t is None else val_entry_date(W, ("team", inc.closer), S, m).isoformat()
        out.append(Q("T4", f"С какой даты сотрудник, закрывший инцидент {c}, числится в команде, в которой "
                     f"работает на дату среза?", "date", f4, (c, inc.closer)))

        def f6(m, inc=inc):
            t, d, _ = owner_at(inc, m)
            return W.tname(t, d, m)
        out.append(Q("T6", f"Как называлась на момент начала инцидента {c} команда, которой принадлежал "
                     f"пострадавший в нём сервис?", "name", f6, (c,)))

        def f7(m, inc=inc):
            return W.code(W.inc_svc(inc, m), S, m)
        out.append(Q("T7", f"Какое кодовое имя на дату среза носит сервис, пострадавший в инциденте {c}?",
                     "name", f7, (c,)))

        def f12(m, inc=inc):
            t = W.val(("team", inc.closer), S, m)
            dd_ = val_entry_date(W, ("name", t), S, m)
            return None if dd_ is None else dd_.isoformat()
        out.append(Q("T12", f"С какой даты команда, в которой на дату среза работает сотрудник, закрывший "
                     f"инцидент {c}, носит своё нынешнее название?", "date", f12, (c, inc.closer)))

    for t in range(len(TEAM_NAMES)):
        for e in W.hist[("name", t)]:
            if e.item is None or e.item.cancelled or e.item.eff > S:
                continue

            def f2(m, t=t):
                return W.val(("phone", W.val(("dep", t), S, m)), S, m)
            out.append(Q("T2", f"Какой внутренний телефонный номер на дату среза у заместителя руководителя "
                         f"команды, которую переименовали в {q(e.value, 'a')}?", "int", f2, (t,)))

    for s in range(len(SERVICES)):
        for e in W.hist[("code", s)]:
            if e.item is None or e.item.cancelled or e.item.eff > S:
                continue

            def f9(m, s=s):
                return W.fio(W.val(("dep", W.val(("owner", s), S, m)), S, m))
            out.append(Q("T9", f"Кто на дату среза является заместителем руководителя команды, которой принадлежит "
                         f"сервис, прежде носивший кодовое имя «{e.item.data['old']}»?", "fio", f9, (s,)))

    # даты для вопросов о состоянии команды
    tdates: dict[int, set] = {t: set() for t in range(len(TEAM_NAMES))}
    for sb in W.substs:
        if not sb.cancelled:
            for _ in range(2):
                tdates[sb.tid].add(rdate(R, sb.start, sb.end))
    for it in W.items:
        x = it.data
        teams = [x[k] for k in ("a", "b", "tid") if isinstance(x.get(k), int)]
        if it.kind not in ("transfer", "lead", "deputy", "owner", "rename", "leave", "hire") or it.eff > S:
            continue
        lo, hi = sorted([it.orig, it.eff])
        for t in teams:
            if it.sign < it.eff:
                tdates[t].add(rdate(R, it.sign, it.eff - timedelta(days=1)))
            if lo < hi:
                tdates[t].add(rdate(R, lo, hi - timedelta(days=1)))
            if it.cancelled:
                tdates[t].add(rdate(R, it.eff, min(S, it.eff + timedelta(days=60))))
    for t in tdates:
        for _ in range(3):
            tdates[t].add(rdate(R, date(2024, 6, 1), S))
    for t in range(len(TEAM_NAMES)):
        for D in sorted(tdates[t]):
            if not date(2024, 3, 1) <= D <= S:
                continue
            nmD, nmS = W.tname(t, D), W.tname(t, S)

            def f5(m, t=t, D=D):
                return len(W.owned(t, W.at(D, m), m))
            out.append(Q("T5", f"Сколько сервисов было закреплено {dlong(D)[:-3]} года за командой, которая на дату "
                         f"среза называется {q(nmS)}?", "int", f5, (t, D)))

            def f8(m, t=t, D=D):
                return W.val(("city", W.lead_actual(t, W.at(D, m), m)), S, m)
            out.append(Q("T8", f"В каком городе на дату среза работает сотрудник, который руководил командой "
                         f"{q(nmD)} по состоянию на {dlong(D)}?", "name", f8, (t, D)))

            def f10(m, t=t, D=D):
                return len(W.members(t, W.at(D, m), m))
            out.append(Q("T10", f"Сколько сотрудников, включая руководителя, числилось {dlong(D)[:-3]} года в команде, "
                          f"которая тогда называлась {q(nmD)}?", "int", f10, (t, D)))

            def f13(m, t=t, D=D):
                return W.val(("phone", W.lead_actual(t, W.at(D, m), m)), S, m)
            out.append(Q("T13", f"Какой внутренний телефонный номер на дату среза у сотрудника, который руководил "
                          f"командой {q(nmD)} по состоянию на {dlong(D)}?", "int", f13, (t, D)))

    pairs = set()
    for inc in W.incidents:
        t, d, dt = owner_at(inc, TRUE)
        pairs.add((dt.year, dt.month, W.lead_actual(t, d)))
    for (y, mth, pid0) in sorted(pairs):
        for p in [W.people[pid0]]:

            def f11(m, y=y, mth=mth, pid=p.pid):
                hits = []
                for inc in W.incidents:
                    t, d, dt = owner_at(inc, m)
                    if (dt.year, dt.month) == (y, mth) and W.lead_actual(t, d, m) == pid:
                        hits.append(inc.code)
                return hits[0] if len(hits) == 1 else ("; ".join(hits) or None)
            out.append(Q("T11", f"Какой инцидент, начавшийся в {MONTHS_P[mth - 1]} {y} года, затронул сервис, "
                          f"принадлежавший в момент начала инцидента команде, которой тогда " +
                          gfill("[руководил|руководила] ", p) + f"{p.full()}?", "name", f11, (p.pid, y, mth)))
    return out


QUOTAS = {"T1": 6, "T2": 3, "T3": 6, "T4": 4, "T5": 3, "T6": 4, "T7": 3, "T8": 3, "T9": 2, "T10": 3,
          "T11": 1, "T12": 1, "T13": 1}


def valid_truth(W: World, qq: Q, a: str) -> bool:
    return bool(a) and ";" not in a


def select_questions(W: World, R) -> list[tuple[Q, str, dict]]:
    cands = candidates(W, R)
    R.shuffle(cands)
    pool = []
    for qq in cands:
        a = qq.ans(TRUE)
        if not valid_truth(W, qq, a):
            continue
        if qq.tpl in ("T4", "T8", "T13"):
            pid = qq.key[1] if qq.tpl == "T4" else None
            if pid is not None and not W.val(("emp", pid), SNAPSHOT):
                continue
        if qq.tpl == "T12" and not W.val(("emp", qq.key[1]), SNAPSHOT):
            continue
        if qq.tpl in ("T8", "T13"):
            ld = W.lead_actual(qq.key[0], qq.key[1])
            if not W.val(("emp", ld), SNAPSHOT):
                continue
        diffs = {k: qq.ans(m) != a for k, m in NAIVE_MODES.items()}
        pool.append((qq, a, diffs))
    chosen: list = []
    counts = {k: 0 for k in NAIVE_MODES}
    left = dict(QUOTAS)
    used_keys: dict = {}
    target = 9
    while any(left.values()):
        best, best_s = None, -1.0
        for i, (qq, _a, diffs) in enumerate(pool):
            if left.get(qq.tpl, 0) <= 0 or qq.text in used_keys:
                continue
            inc_use = sum(1 for c in chosen if c[0].key[:1] == qq.key[:1] and isinstance(qq.key[0], str))
            if inc_use >= 2:
                continue
            if qq.tpl in ("T4", "T12") and any(c[0].tpl in ("T4", "T12") and c[0].key[1] == qq.key[1]
                                               for c in chosen):
                continue
            s = sum((max(0, target - counts[k]) + 0.2) for k, v in diffs.items() if v)
            s += R.random() * 0.5
            if s > best_s:
                best, best_s = i, s
        if best is None:
            raise RuntimeError("не хватает кандидатов для вопросов")
        qq, a, diffs = pool.pop(best)
        chosen.append((qq, a, diffs))
        used_keys[qq.text] = 1
        left[qq.tpl] -= 1
        for k, v in diffs.items():
            counts[k] += v
    R.shuffle(chosen)
    return chosen


# --------------------------------------------------------------------------
# Правила, сборка, проверка
# --------------------------------------------------------------------------

RULES = """# Вопросы по интранету ООО «Северный Вектор»

Выгрузка интранета лежит в каталоге `intranet/` (стартовая страница — `intranet/index.html`).
Срез сделан 30 июня 2026 года. Ответьте на вопросы в конце файла, пользуясь только материалами
интранета и правилами ниже. Документы написаны свободным текстом, у одних и тех же фактов бывают
разные формулировки; важен каждый документ.

## Правила

1. **Дата среза** — 30.06.2026. «Сейчас», «на дату среза», «нынешний» означают состояние на 30.06.2026.
2. **Приказы.** Переименования команд, назначения руководителей и заместителей, переводы сотрудников
   между командами, приём и увольнение, передача сервисов между командами, смена кодовых имён сервисов,
   смена офиса сотрудника и временное исполнение обязанностей руководителя происходят только по приказам
   (раздел «Приказы»). Изменение действует с даты вступления в силу, указанной в пункте приказа,
   включительно. Если в пункте дата не указана, изменение действует с даты подписания приказа.
   Изменение, вступающее в силу после 30.06.2026, на дату среза ещё не действует.
3. **Изменения и отмены приказов.** Более поздний приказ может перенести дату вступления в силу
   более раннего приказа, продлить или сократить срок временного исполнения обязанностей либо отменить
   приказ или его пункт. Отменённый приказ (пункт) не действовал ни одного дня.
4. **Увольнение и приём.** Сотрудник числится в компании и в своей команде по последний рабочий день
   включительно; со следующего дня — нет. Принятый сотрудник числится с первого рабочего дня.
5. **Протоколы встреч и новости** не являются основанием для оргизменений: намерения, предложения и
   договорённости из них не учитываются. Исключения: (а) внутренние телефонные номера меняются по
   объявлениям в разделе «Новости» — новый номер действует с даты, указанной в объявлении;
   (б) исправления к отчётам об инцидентах (п. 8).
6. **Справочные страницы** (карточки сотрудников, страницы команд и сервисов, глоссарий, каталоги)
   отражают состояние на дату обновления, указанную внизу страницы; более поздние изменения на них
   могут быть не отражены. Страницы с пометкой «Устарело» источником не считаются.
7. **Руководитель на дату.** «Руководил командой», «руководитель команды на дату» — сотрудник,
   фактически исполнявший обязанности руководителя: если на эту дату действовал приказ о временном
   исполнении обязанностей руководителя команды, то исполняющий обязанности (обе граничные даты срока
   включаются), иначе — назначенный руководитель. Заместитель руководителя — назначенный заместитель.
8. **Инциденты.** Момент инцидента — поле «Начало инцидента» отчёта (время московское). Отчёт может
   содержать раздел «Исправления»; кроме того, исправления фиксируются в протоколах разборов инцидентов
   в разделе «Исправления к отчётам». Исправление имеет приоритет над исходным текстом отчёта.
   «Сервис, пострадавший в инциденте» — сервис из поля «Затронутый сервис» с учётом исправлений.
   Команда, которой принадлежит сервис, определяется на момент (дату) начала инцидента.
9. **Дежурства.** Дежурная неделя начинается в понедельник в 10:00 и заканчивается в 10:00 следующего
   понедельника; в таблице графика неделя обозначена датами понедельника и воскресенья. Дежурный в момент
   инцидента — основной дежурный по графику той команды, которой принадлежал сервис на дату начала
   инцидента, с учётом замен, перечисленных на той же странице графика. Резервный дежурный не считается.
10. **Численность команды** на дату — все сотрудники, числящиеся в команде на эту дату, включая
    руководителя и заместителя; временное исполнение обязанностей состав команды не меняет.
11. Команда после переименования остаётся той же командой; сервис после смены кодового имени — тем же
    сервисом. Официальные названия сервисов не меняются.

## Формат ответов

Запишите ответы в файл `answers.json` в корне рабочего каталога: JSON-объект
`{"Q01": "...", "Q02": "...", ...}` со всеми 40 ключами. Тип ответа указан после вопроса:

- **ФИО** — полностью, в именительном падеже: «Фамилия Имя Отчество»;
- **число** — только цифры;
- **дата** — в формате ГГГГ-ММ-ДД;
- **название** — название команды, кодовое имя, город или код инцидента ровно так, как в интранете,
  в именительном падеже, без кавычек.

## Вопросы

"""

KIND_LABEL = {"fio": "ФИО", "int": "число", "date": "дата", "name": "название"}


@functools.cache
def build():
    R = rng(TASK_ID)
    W = Gen(R).run()
    chosen = select_questions(W, R)
    files = Render(W, R).site()
    lines = []
    answers, kinds, naive = {}, {}, {k: {} for k in NAIVE_MODES}
    for i, (qq, a, _) in enumerate(chosen, 1):
        qid = f"Q{i:02d}"
        lines.append(f"**{qid}.** {qq.text} *(ответ: {KIND_LABEL[qq.kind]})*\n")
        answers[qid], kinds[qid] = a, qq.kind
        for k, m in NAIVE_MODES.items():
            naive[k][qid] = qq.ans(m)
    files["questions.md"] = RULES + "\n".join(lines)
    return {"files": files, "answers": answers, "kinds": kinds, "naive": naive, "world": W,
            "chosen": chosen}


def _canon(kind: str, v) -> object:
    if kind == "int":
        x = num(v)
        return None if x is None else round(x)
    s = norm(v)
    if kind == "fio":
        return tuple(sorted(re.findall(r"[a-zа-я]+", s)))
    if kind == "date":
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s) or None
        if m:
            return (int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
        return (int(m[3]), int(m[2]), int(m[1])) if m else s
    s = s.replace("«", "").replace("»", "").replace('"', "")
    s = re.sub(r"^(команда|сервис|город|г\.)\s+", "", s)
    return s.strip()


def setup(ws: Path) -> None:
    for rel, text in build()["files"].items():
        write(ws, rel, text)


def gold(ws: Path) -> None:
    write_json(ws, "answers.json", build()["answers"])


def check(ws: Path) -> str:
    b = build()
    got = read_json(ws, "answers.json")
    assert isinstance(got, dict), "answers.json должен содержать JSON-объект"
    ok, errs = 0, []
    for qid, exp in b["answers"].items():
        kind = b["kinds"][qid]
        if qid in got and _canon(kind, got[qid]) == _canon(kind, exp):
            ok += 1
        else:
            errs.append(f"{qid} неверно" if qid in got else f"{qid} нет ответа")
    return require_share(len(b["answers"]), ok, min_share=MIN_CORRECT / N_QUESTIONS, what="ответы", errors=errs)


PROMPT = (
    "В каталоге intranet/ — выгрузка внутреннего сайта компании «Северный Вектор»: карточки сотрудников, "
    "страницы команд и сервисов, приказы, отчёты об инцидентах, графики дежурств, протоколы встреч, новости, "
    "глоссарий. В файле questions.md — правила толкования материалов и 40 вопросов, каждый требует связать "
    "несколько документов с учётом дат. Ответь на все вопросы строго по материалам и правилам из "
    "questions.md и запиши ответы в answers.json в корне рабочего каталога — JSON-объект вида "
    '{"Q01": "...", ..., "Q40": "..."}; формат каждого ответа (ФИО, число, дата ГГГГ-ММ-ДД, название) указан '
    "в questions.md. Материалов много, документы написаны свободным текстом, более поздние документы могут "
    "уточнять или отменять более ранние — учитывай каждый документ. Файлы в intranet/ не изменяй."
)


def _naive(mode_name: str):
    def damage(ws: Path) -> None:
        write_json(ws, "answers.json", build()["naive"][mode_name])
    damage.__name__ = f"naive_{mode_name}"
    return damage


def short_fio(ws: Path) -> None:
    """ФИО инициалами вместо полного."""
    b = build()
    out = dict(b["answers"])
    for qid, k in b["kinds"].items():
        if k == "fio":
            parts = out[qid].split()
            out[qid] = f"{parts[0]} {parts[1][0]}. {parts[2][0]}."
    write_json(ws, "answers.json", out)


NEAR_MISSES = [_naive(k) for k in NAIVE_MODES] + [short_fio]

TASK = long_task(
    id="task_393_intranet_multihop",  # registry id; TASK_ID stays the generator seed
    name="Многошаговые вопросы по интранету компании с учётом дат",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("reading", "multihop", "html", "dates"),
)
