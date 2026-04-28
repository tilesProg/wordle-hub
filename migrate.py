"""
Скрипт миграции базы данных.

Переносит данные из старой схемы (user, game, category, list_item, word_entry)
в новую схему (users, games, categories, list_items, words, word_values).

Запустить один раз:
    python migrate.py

Перед миграцией создаётся резервная копия db.sqlite3.backup.
"""
import os
import shutil
import sqlite3

DB_PATH   = os.path.join(os.path.dirname(__file__), 'db.sqlite3')
DB_BACKUP = os.path.join(os.path.dirname(__file__), 'db.sqlite3.backup')


def _parse_cat_values(text: str) -> dict:
    """Разбирает старый формат ';;'-разделённой строки в {cat_id: [values]}."""
    result = {}
    if not text:
        return result
    for part in text.split(';;'):
        if ':' not in part:
            continue
        cid_str, vals_str = part.split(':', 1)
        result[int(cid_str)] = vals_str.split('|')
    return result


def main():
    if not os.path.exists(DB_PATH):
        print('db.sqlite3 не найдена — нечего мигрировать.')
        return

    # Создаём резервную копию
    shutil.copy2(DB_PATH, DB_BACKUP)
    print(f'Резервная копия сохранена: {DB_BACKUP}')

    # Читаем данные из старой схемы
    backup = sqlite3.connect(DB_BACKUP)
    backup.row_factory = sqlite3.Row

    try:
        users      = backup.execute('SELECT * FROM user').fetchall()
        games      = backup.execute('SELECT * FROM game').fetchall()
        categories = backup.execute('SELECT * FROM category').fetchall()
        list_items = backup.execute('SELECT * FROM list_item').fetchall()
        words      = backup.execute('SELECT * FROM word_entry').fetchall()
    except sqlite3.OperationalError as exc:
        print(f'Ошибка чтения старой схемы: {exc}')
        print('База данных уже может быть на новой схеме.')
        backup.close()
        return

    cat_kinds = {row['id']: row['kind'] for row in categories}
    backup.close()

    # Перезаписываем основную БД с новой схемой
    conn = sqlite3.connect(DB_PATH)

    # Удаляем старые таблицы (порядок важен из-за FK)
    for table in ('word_entry', 'list_item', 'category', 'game', 'user'):
        conn.execute(f'DROP TABLE IF EXISTS {table}')

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            username      VARCHAR(80)  NOT NULL UNIQUE,
            password_hash VARCHAR(256) NOT NULL
        );
        CREATE TABLE IF NOT EXISTS games (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR(140) NOT NULL,
            description TEXT,
            creator_id  INTEGER NOT NULL REFERENCES users(id),
            is_hidden   BOOLEAN DEFAULT 0,
            access      VARCHAR(20) DEFAULT 'public',
            link_token  VARCHAR(64) UNIQUE,
            max_guesses INTEGER
        );
        CREATE TABLE IF NOT EXISTS categories (
            id       INTEGER PRIMARY KEY,
            game_id  INTEGER NOT NULL REFERENCES games(id),
            name     VARCHAR(120) NOT NULL,
            kind     VARCHAR(10)  NOT NULL,
            position INTEGER      NOT NULL
        );
        CREATE TABLE IF NOT EXISTS list_items (
            id          INTEGER PRIMARY KEY,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            value       VARCHAR(200) NOT NULL
        );
        CREATE TABLE IF NOT EXISTS words (
            id         INTEGER PRIMARY KEY,
            game_id    INTEGER NOT NULL REFERENCES games(id),
            name       VARCHAR(200) NOT NULL,
            created_at DATETIME
        );
        CREATE TABLE IF NOT EXISTS word_values (
            id           INTEGER PRIMARY KEY,
            word_id      INTEGER NOT NULL REFERENCES words(id),
            category_id  INTEGER NOT NULL REFERENCES categories(id),
            value_text   TEXT,
            value_number REAL,
            value_date   DATETIME,
            value_group  VARCHAR(200)
        );
    """)
    conn.commit()

    # Переносим данные
    conn.executemany(
        'INSERT INTO users (id, username, password_hash) VALUES (?,?,?)',
        [(u['id'], u['username'], u['password_hash']) for u in users],
    )
    conn.executemany(
        'INSERT INTO games (id, name, description, creator_id, is_hidden, access, link_token, max_guesses)'
        ' VALUES (?,?,?,?,?,?,?,?)',
        [(g['id'], g['name'], g['description'], g['creator_id'],
          g['is_hidden'], g['access'], g['link_token'], g['max_guesses'])
         for g in games],
    )
    conn.executemany(
        'INSERT INTO categories (id, game_id, name, kind, position) VALUES (?,?,?,?,?)',
        [(c['id'], c['game_id'], c['name'], c['kind'], c['position']) for c in categories],
    )
    conn.executemany(
        'INSERT INTO list_items (id, category_id, value) VALUES (?,?,?)',
        [(li['id'], li['category_id'], li['value']) for li in list_items],
    )

    # Конвертируем word_entry → words + word_values
    wv_rows = []
    for we in words:
        conn.execute(
            'INSERT INTO words (id, game_id, name, created_at) VALUES (?,?,?,?)',
            (we['id'], we['game_id'], we['name'], we['created_at']),
        )
        for cat_id, vals in _parse_cat_values(we['cat_values']).items():
            kind = cat_kinds.get(cat_id)
            if kind == 'list':
                for v in vals:
                    if v:   # сохраняем 'Not specified' как есть
                        wv_rows.append((we['id'], cat_id, None, None, None, v))
            elif kind == 'date':
                v = vals[0] if vals else ''
                date_val = v if (v and v != 'Not specified') else None
                if date_val:
                    wv_rows.append((we['id'], cat_id, None, None, date_val, None))
            elif kind == 'number':
                v = vals[0] if vals else ''
                try:
                    num = float(v) if (v and v != 'Not specified') else None
                except (ValueError, TypeError):
                    num = None
                if num is not None:
                    wv_rows.append((we['id'], cat_id, None, num, None, None))

    if wv_rows:
        conn.executemany(
            'INSERT INTO word_values'
            ' (word_id, category_id, value_text, value_number, value_date, value_group)'
            ' VALUES (?,?,?,?,?,?)',
            wv_rows,
        )

    conn.commit()
    conn.close()

    print('Миграция завершена успешно.')
    print(f'  users:       {len(users)}')
    print(f'  games:       {len(games)}')
    print(f'  categories:  {len(categories)}')
    print(f'  list_items:  {len(list_items)}')
    print(f'  words:       {len(words)}')
    print(f'  word_values: {len(wv_rows)}')


if __name__ == '__main__':
    main()
