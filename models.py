"""
Проектирование базы данных.

Таблицы соответствуют схеме ER-диаграммы:
  users, games, categories, list_items, words, word_values
"""
from datetime import datetime

from werkzeug.security import check_password_hash

from extensions import db


# ── Модели ───────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    games         = db.relationship('Game', backref='creator', lazy=True)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)


class Game(db.Model):
    __tablename__ = 'games'

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, default='')
    creator_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_hidden   = db.Column(db.Boolean, default=False)
    access      = db.Column(db.String(20), default='public')   # public | link | private
    link_token  = db.Column(db.String(64), unique=True, nullable=True)
    max_guesses = db.Column(db.Integer, nullable=True)
    categories  = db.relationship(
        'Category', backref='game', lazy=True,
        cascade='all, delete-orphan', order_by='Category.position',
    )
    words = db.relationship('Word', backref='game', lazy=True, cascade='all, delete-orphan')


class Category(db.Model):
    __tablename__ = 'categories'

    id       = db.Column(db.Integer, primary_key=True)
    game_id  = db.Column(db.Integer, db.ForeignKey('games.id'), nullable=False)
    name     = db.Column(db.String(120), nullable=False)
    kind     = db.Column(db.String(10), nullable=False)   # list | date | number
    position = db.Column(db.Integer, nullable=False)
    list_items = db.relationship(
        'ListItem', backref='category', lazy=True, cascade='all, delete-orphan',
    )


class ListItem(db.Model):
    __tablename__ = 'list_items'

    id          = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    value       = db.Column(db.String(200), nullable=False)


class Word(db.Model):
    __tablename__ = 'words'

    id         = db.Column(db.Integer, primary_key=True)
    game_id    = db.Column(db.Integer, db.ForeignKey('games.id'), nullable=False)
    name       = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    values     = db.relationship('WordValue', backref='word', lazy=True, cascade='all, delete-orphan')

    @property
    def values_by_category(self) -> dict:
        """Возвращает {category_id: list|str} для отображения в шаблонах."""
        result = {}
        for wv in self.values:
            cid = wv.category_id
            if wv.value_group is not None:
                result.setdefault(cid, []).append(wv.value_group)
            elif wv.value_date is not None:
                result[cid] = wv.value_date.isoformat()
            elif wv.value_number is not None:
                n = wv.value_number
                result[cid] = str(int(n) if n == int(n) else n)
        return result


class WordValue(db.Model):
    __tablename__ = 'word_values'

    id           = db.Column(db.Integer, primary_key=True)
    word_id      = db.Column(db.Integer, db.ForeignKey('words.id'), nullable=False)
    category_id  = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    value_text   = db.Column(db.Text)           # зарезервировано для текстового типа
    value_number = db.Column(db.Float)          # для категорий kind='number'
    value_date   = db.Column(db.DateTime)       # для категорий kind='date'
    value_group  = db.Column(db.String(200))    # для категорий kind='list'


# ── Вспомогательные функции для работы со значениями слов ───────────────────

def set_word_values(word: Word, categories: list, mapping: dict) -> None:
    """Записывает WordValue-строки из словаря {cat_id: str | list[str]}.

    Предварительно удаляет все существующие значения для данного слова.
    """
    WordValue.query.filter_by(word_id=word.id).delete()

    for cat in categories:
        val = mapping.get(cat.id) or mapping.get(str(cat.id))

        if cat.kind == 'list':
            items = val if isinstance(val, list) else ([val] if val else [])
            for item in items:
                if item:
                    db.session.add(WordValue(
                        word_id=word.id, category_id=cat.id, value_group=str(item),
                    ))

        elif cat.kind == 'date':
            raw = (val[0] if isinstance(val, list) else val) or ''
            date_obj = None
            if raw and raw not in ('', 'Not specified'):
                try:
                    date_obj = datetime.fromisoformat(raw)
                except (ValueError, TypeError):
                    pass
            if date_obj is not None:
                db.session.add(WordValue(
                    word_id=word.id, category_id=cat.id, value_date=date_obj,
                ))

        elif cat.kind == 'number':
            raw = (val[0] if isinstance(val, list) else val) or ''
            raw = str(raw) if not isinstance(raw, str) else raw
            num = None
            if raw and raw not in ('', 'Not specified'):
                try:
                    num = float(raw)
                except (ValueError, TypeError):
                    pass
            if num is not None:
                db.session.add(WordValue(
                    word_id=word.id, category_id=cat.id, value_number=num,
                ))


def get_word_values(word: Word, categories: list) -> dict:
    """Возвращает {cat_id: типизированное_значение} для игровой логики.

    - kind='list'   → list[str]  (может содержать 'Not specified')
    - kind='date'   → datetime | None
    - kind='number' → float | None
    """
    by_cat: dict[int, list] = {}
    for wv in word.values:
        by_cat.setdefault(wv.category_id, []).append(wv)

    result = {}
    for cat in categories:
        wvs = by_cat.get(cat.id, [])
        if cat.kind == 'list':
            result[cat.id] = [wv.value_group for wv in wvs if wv.value_group]
        elif cat.kind == 'date':
            result[cat.id] = wvs[0].value_date if wvs else None
        elif cat.kind == 'number':
            result[cat.id] = wvs[0].value_number if wvs else None
    return result


def get_word_values_api(word: Word, categories: list) -> dict:
    """Возвращает {str(cat_id): [str, ...]} для JSON-ответов API.

    Все значения обёрнуты в список, чтобы клиентский JS работал единообразно.
    """
    by_cat: dict[int, list] = {}
    for wv in word.values:
        by_cat.setdefault(wv.category_id, []).append(wv)

    result = {}
    for cat in categories:
        wvs = by_cat.get(cat.id, [])
        key = str(cat.id)

        if cat.kind == 'list':
            vals = [wv.value_group for wv in wvs if wv.value_group]
            result[key] = vals if vals else ['Not specified']

        elif cat.kind == 'date':
            wv = wvs[0] if wvs else None
            result[key] = [wv.value_date.isoformat() if (wv and wv.value_date) else 'Not specified']

        elif cat.kind == 'number':
            wv = wvs[0] if wvs else None
            if wv and wv.value_number is not None:
                n = wv.value_number
                result[key] = [str(int(n) if n == int(n) else n)]
            else:
                result[key] = ['Not specified']

    return result
