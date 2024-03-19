# Copyright 2023-2024 Vlad Krupinskii <mrvladus@yandex.ru>
# SPDX-License-Identifier: MIT

from dataclasses import dataclass, asdict, field
import json
import os
import shutil
import sqlite3
import threading
from typing import Any, Iterable, Self, get_type_hints
from uuid import uuid4

from gi.repository import GLib  # type:ignore

from errands.lib.gsettings import GSettings
from errands.lib.logging import Log
from errands.lib.utils import threaded, timeit


@dataclass
class TaskListData:
    deleted: bool
    name: str
    synced: bool
    uid: str


@dataclass
class TaskData:
    color: str = ""
    completed: bool = False
    deleted: bool = False
    end_date: str = ""
    expanded: bool = False
    list_uid: str = ""
    notes: str = ""
    parent: str = ""
    percent_complete: int = 0
    priority: int = 0
    start_date: str = ""
    synced: bool = False
    tags: str = ""
    text: str = ""
    toolbar_shown: bool = False
    trash: bool = False
    uid: str = ""


@dataclass
class ErrandsData:
    lists: list[TaskListData] = field(default_factory=list)
    tasks: list[TaskData] = field(default_factory=list)


class UserDataJSON:

    def __init__(self) -> None:
        self.__data_dir: str = os.path.join(GLib.get_user_data_dir(), "errands")
        self.__data_file_path: str = os.path.join(self.__data_dir, "data.json")
        self.__task_lists_data: list[TaskListData] = []
        self.__tasks_data: list[TaskData] = []

    # ------ PROPERTIES ------ #

    @property
    def task_lists(self) -> list[TaskListData]:
        return self.__task_lists_data

    @task_lists.setter
    def task_lists(self, lists_data: list[TaskListData]):
        self.__task_lists_data = lists_data
        self.__write_data()

    @property
    def tasks(self) -> list[TaskData]:
        return self.__tasks_data

    @tasks.setter
    def tasks(self, tasks_data: list[TaskData]):
        self.__tasks_data = tasks_data
        self.__write_data()

    # ------ PUBLIC METHODS ------ #

    def init(self) -> None:
        if not os.path.exists(self.__data_dir):
            os.mkdir(self.__data_dir)
        if not os.path.exists(self.__data_file_path):
            self.__write_data()
        else:
            self.__read_data()

    def add_list(
        self, name: str, uuid: str = None, synced: bool = False
    ) -> TaskListData:
        uid: str = str(uuid4()) if not uuid else uuid

        Log.debug(f"Data: Create list '{uid}'")

        new_list = TaskListData(deleted=False, name=name, uid=uid, synced=synced)
        data: list[TaskListData] = self.task_lists
        data.append(new_list)
        self.task_lists = data

        return new_list

    def add_task(self, **kwargs) -> TaskData:
        data: list[TaskData] = self.tasks
        new_task = TaskData(**kwargs)
        if not new_task.uid:
            new_task.uid = str(uuid4())
        if not GSettings.get("task-list-new-task-position-top"):
            data.append(new_task)
        else:
            data.insert(0, new_task)
        self.tasks = data

        return new_task

    def clean_deleted(self) -> None:
        pass

    def get_lists_as_dicts(self) -> list[TaskListData]:
        return self.task_lists

    def get_prop(self, list_uid: str, uid: str, prop: str) -> Any:
        tasks: list[TaskData] = self.tasks
        for t in tasks:
            if t.list_uid == list_uid and t.uid == uid:
                task = t
                break

        return getattr(task, prop)

    def get_tasks_as_dicts(
        self, list_uid: str = None, parent: str = None
    ) -> list[TaskData]:
        if not list_uid:
            return self.tasks
        elif list_uid and not parent:
            return [t for t in self.tasks if t.list_uid == list_uid]
        elif list_uid and parent:
            return [
                t for t in self.tasks if t.list_uid == list_uid and t.parent == parent
            ]

    def move_task_after(
        self, list_uid: str, task_uid: str, task_after_uid: str
    ) -> None:
        tasks: list[TaskData] = self.tasks

        # Get indexes
        for task in tasks:
            if task.list_uid == list_uid:
                if task.uid == task_uid:
                    task_idx: int = tasks.index(task)
                elif task.uid == task_after_uid:
                    task_after_idx: int = tasks.index(task)

        # Swap items
        if task_idx < task_after_idx:
            i = task_idx
            while i < task_after_idx:
                tasks[i], tasks[i + 1] = tasks[i + 1], tasks[i]
                i += 1
        else:
            i = task_idx
            while task_after_idx + 1 > i:
                tasks[i], tasks[i - 1] = tasks[i - 1], tasks[i]
                i -= 1

        # Save tasks
        self.tasks = tasks

    def move_task_before(
        self, list_uid: str, task_uid: str, task_before_uid: str
    ) -> None:
        tasks: list[TaskData] = self.tasks
        # Get indexes
        for task in tasks:
            if task.list_uid == list_uid:
                if task.uid == task_uid:
                    task_idx: int = tasks.index(task)
                elif task.uid == task_before_uid:
                    task_before_idx: int = tasks.index(task)

        # Swap items
        if task_idx < task_before_idx:
            i = task_idx
            while i < task_before_idx - 1:
                tasks[i], tasks[i + 1] = tasks[i + 1], tasks[i]
                i += 1
        else:
            i = task_idx
            while task_before_idx > i:
                tasks[i], tasks[i - 1] = tasks[i - 1], tasks[i]
                i -= 1

        # Save tasks
        self.tasks = tasks

    def update_props(
        self, list_uid: str, uid: str, props: Iterable[str], values: Iterable[Any]
    ):
        tasks = self.tasks
        for task in tasks:
            if task.list_uid == list_uid and task.uid == uid:
                for idx, prop in enumerate(props):
                    setattr(task, prop, values[idx])
                break
        self.tasks = tasks

    # ------ PRIVATE METHODS ------ #

    def __read_data(self) -> None:
        try:
            with open(self.__data_file_path, "r") as f:
                data: dict[str, Any] = json.load(f)
                self.task_lists = [TaskListData(**l) for l in data["lists"]]
                self.tasks = [TaskData(**t) for t in data["tasks"]]
        except Exception as e:
            Log.error(
                f"Data: Can't read data file from disk. {e}. Creating new data file"
            )
            self.__write_data()

    def __write_data(self) -> None:
        try:
            with open(self.__data_file_path, "w") as f:
                lists: list[dict] = [asdict(l) for l in self.task_lists]
                tasks: list[dict] = [asdict(t) for t in self.tasks]
                data: dict = {"lists": lists, "tasks": tasks}
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            Log.error(f"Data: Can't write to disk. {e}.")


# TODO
class SQLBuilder:
    def __init__(self) -> None:
        self.query: str = ""

    def create_table(self, name: str) -> Self:
        self.query += f"CREATE TABLE IF NOT EXISTS {name} "
        return self

    # TODO
    def insert(self, into: str, columns: Iterable[str], values: Iterable[Any]) -> Self:
        self.query += f"INSERT INTO {into} ({', '.join(columns)}) VALUES ()"
        return self

    def select(self, query: str, from_table: str) -> Self:
        self.query += f"SELECT {query} FROM {from_table} "
        return self

    def finish(self) -> str:
        return self.query


def create_table_query_from_dict(table_name: str, obj: dict) -> str:
    dict_obj: dict[str, Any] = get_type_hints(obj)
    query: str = f"CREATE TABLE IF NOT EXISTS {table_name} ("
    for item in dict_obj:
        query += item + " "
        if dict_obj[item] == str:
            query += "TEXT NOT NULL, "
        elif dict_obj[item] == bool or dict_obj[item] == int:
            query += "INTEGER NOT NULL, "
    query = query.removesuffix(", ")
    query += ")"
    return query


class UserDataSQLite:
    data_dir: str = os.path.join(GLib.get_user_data_dir(), "errands")
    data_file_path: str = os.path.join(data_dir, "data.db")

    @classmethod
    def init(cls):
        if not os.path.exists(cls.data_dir):
            os.mkdir(cls.data_dir)
        cls.connection = sqlite3.connect(
            cls.data_file_path, check_same_thread=False, isolation_level=None
        )
        cls.execute(create_table_query_from_dict("lists", TaskListData))
        cls.execute(create_table_query_from_dict("tasks", TaskData))
        cls.__convert(cls)

    @classmethod
    def add_list(
        cls, name: str, uuid: str = None, synced: bool = False
    ) -> TaskListData:
        uid: str = str(uuid4()) if not uuid else uuid
        Log.debug(f"Data: Create list '{uid}'")
        with cls.connection:
            cur = cls.connection.cursor()
            cur.execute(
                f"""INSERT INTO lists
                ({", ".join(get_type_hints(TaskListData))})
                VALUES ({", ".join(["?" for _ in get_type_hints(TaskListData)])})""",
                (False, name, synced, uid),
            )
            return TaskListData(uid=uid, name=name, deleted=False, synced=synced)

    @classmethod
    def clean_deleted(cls):
        Log.debug("Data: Clean deleted")

        with cls.connection:
            cur = cls.connection.cursor()
            cur.execute("DELETE FROM lists WHERE deleted = 1")
            cur.execute("DELETE FROM tasks WHERE deleted = 1")

    @classmethod
    def get_lists_as_dicts(cls) -> list[TaskListData]:
        with cls.connection:
            cur = cls.connection.cursor()
            cur.execute("SELECT * FROM lists")
            return [
                TaskListData(
                    **{
                        key: i[idx]
                        for idx, key in enumerate(get_type_hints(TaskListData))
                    }
                )
                for i in cur.fetchall()
            ]

    @classmethod
    def move_task_after(cls, list_uid: str, task_uid: str, after_uid: str) -> None:
        cls.move_task_before(list_uid, task_uid, after_uid)
        cls.__swap_rows(list_uid, task_uid, after_uid)

    @classmethod
    def move_task_before(cls, list_uid: str, task_uid: str, before_uid: str) -> None:
        tasks: list[TaskData] = [
            t for t in cls.get_tasks_as_dicts() if t["list_uid"] == list_uid
        ]
        # Get indexes
        for task in tasks:
            if task["uid"] == task_uid:
                task_idx = tasks.index(task)
            elif task["uid"] == before_uid:
                before_idx = tasks.index(task)

        # Get slice of tasks
        if task_idx < before_idx:
            tasks = tasks[task_idx:before_idx]
        else:
            if before_idx == 0:
                tasks = tasks[task_idx::-1]
            else:
                tasks = tasks[task_idx : before_idx - 1 : -1]

        length: int = len(tasks)
        for i in range(length):
            if i + 1 == length:
                break
            cls.__swap_rows(list_uid, tasks[i]["uid"], tasks[i + 1]["uid"])
            tasks[i], tasks[i + 1] = tasks[i + 1], tasks[i]

    @classmethod
    def move_task_to_list(
        cls,
        task_uid: str,
        old_list_uid: str,
        new_list_uid: str,
        parent: str,
        synced: bool,
    ) -> None:
        sub_tasks_uids: list[str] = cls.__get_sub_tasks_uids_tree(
            old_list_uid, task_uid
        )
        tasks: list[TaskData] = cls.get_tasks_as_dicts(old_list_uid)
        task_dict: TaskData = [i for i in tasks if i["uid"] == task_uid][0]
        sub_tasks_dicts: list[TaskData] = [
            i for i in tasks if i["uid"] in sub_tasks_uids
        ]
        # Move task
        if old_list_uid == new_list_uid:
            cls.run_sql(
                f"DELETE FROM tasks WHERE list_uid = '{old_list_uid}' AND uid = '{task_uid}'"
            )
        else:
            cls.update_props(old_list_uid, task_uid, ["deleted"], [True])
        task_dict["list_uid"] = new_list_uid
        task_dict["parent"] = parent
        task_dict["synced"] = synced
        cls.add_task(**task_dict)
        # Move sub-tasks
        for sub_dict in sub_tasks_dicts:
            if old_list_uid == new_list_uid:
                cls.run_sql(
                    f"""DELETE FROM tasks
                    WHERE list_uid = '{old_list_uid}'
                    AND uid = '{sub_dict['uid']}'"""
                )
            else:
                cls.update_props(old_list_uid, sub_dict["uid"], ["deleted"], [True])
            sub_dict["list_uid"] = new_list_uid
            sub_dict["synced"] = synced
            cls.add_task(**sub_dict)

    @classmethod
    def get_prop(cls, list_uid: str, uid: str, prop: str) -> Any:
        # Log.debug(f"Data: Get '{prop}' for '{uid}'")

        with cls.connection:
            cur = cls.connection.cursor()
            cur.execute(
                f"SELECT {prop} FROM tasks WHERE uid = ? AND list_uid = ?",
                (uid, list_uid),
            )
            return cur.fetchone()[0]

    @classmethod
    def update_props(
        cls, list_uid: str, uid: str, props: list[str], values: list[Any]
    ) -> None:
        with cls.connection:
            cur = cls.connection.cursor()
            query_props = ", ".join([f"{p} = ?" for p in props])
            cur.execute(
                f"""UPDATE tasks SET
                {query_props}
                WHERE uid = '{uid}'
                AND list_uid = '{list_uid}'""",
                tuple(values),
            )

    @classmethod
    def run_sql(cls, *cmds: list, fetch: bool = False) -> list[tuple] | None:
        try:
            with cls.connection:
                cur = cls.connection.cursor()
                for cmd in cmds:
                    if isinstance(cmd, tuple):
                        cur.execute(cmd[0], cmd[1])
                    else:
                        cur.execute(cmd)
                return cur.fetchall() if fetch else None
        except Exception as e:
            Log.error(f"Data: {e}")

    @classmethod
    def get_parents_uids_tree(cls, list_uid: str, task_uid: str) -> list[str]:
        parents_uids: list[str] = []
        parent: str = cls.get_prop(list_uid, task_uid, "parent")
        while parent != "":
            parents_uids.append(parent)
            parent = cls.get_prop(list_uid, parent, "parent")
        return parents_uids

    @classmethod
    def get_tasks_as_dicts(
        cls, list_uid: str = None, parent: str = None
    ) -> list[TaskData]:
        with cls.connection:
            cur = cls.connection.cursor()
            if not list_uid:
                cur.execute("SELECT * FROM tasks")
            else:
                cur.execute(
                    f"""SELECT * FROM tasks WHERE list_uid = ?
                    {f"AND parent = '{parent}'" if parent else ""}""",
                    (list_uid,),
                )
            return [
                TaskData(
                    **{key: i[idx] for idx, key in enumerate(get_type_hints(TaskData))}
                )
                for i in cur.fetchall()
            ]

    @classmethod
    def add_task(
        cls,
        color: str = "",
        completed: bool = False,
        deleted: bool = False,
        end_date: str = "",
        expanded: bool = False,
        list_uid: str = "",
        notes: str = "",
        parent: str = "",
        percent_complete: int = 0,
        priority: int = 0,
        start_date: str = "",
        synced: bool = False,
        tags: str = "",
        text: str = "",
        toolbar_shown: bool = False,
        trash: bool = False,
        uid: str = "",
        insert_at_the_top: bool = False,
    ) -> str:
        if not uid:
            uid: str = str(uuid4())

        Log.debug(f"Data: Add task {uid}")

        with cls.connection:
            cur = cls.connection.cursor()
            cur.execute(
                """INSERT INTO tasks 
                (uid, list_uid, text, parent, completed, deleted, color, notes, percent_complete, priority, start_date, end_date, tags, synced, expanded, trash, toolbar_shown) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uid,
                    list_uid,
                    text,
                    parent,
                    completed,
                    deleted,
                    color,
                    notes,
                    percent_complete,
                    priority,
                    start_date,
                    end_date,
                    tags,
                    synced,
                    expanded,
                    trash,
                    toolbar_shown,
                ),
            )
            return uid

    # --- PRIVATE METHODS --- #

    @classmethod
    def __swap_rows(cls, list_uid: str, uid_1: str, uid_2: str) -> None:
        try:
            with cls.connection:
                cur = cls.connection.cursor()
                # Get rows
                row1 = cur.execute(
                    f"SELECT * FROM tasks WHERE list_uid = ? AND uid = ?",
                    (list_uid, uid_1),
                ).fetchone()
                row1_rowid = cls.__get_rowid(list_uid, uid_1)
                row2 = cur.execute(
                    f"SELECT * FROM tasks WHERE list_uid = ? AND uid = ?",
                    (list_uid, uid_2),
                ).fetchone()
                row2_rowid = cls.__get_rowid(list_uid, uid_2)
                # Update the rows with each other's data
                props_query = ", ".join(
                    [f"{p} = ?" for p in list(get_type_hints(TaskData))]
                )
                cur.execute(
                    f"UPDATE tasks SET {props_query} WHERE rowid = ?", row2 + row1_rowid
                )
                cur.execute(
                    f"UPDATE tasks SET {props_query} WHERE rowid = ?", row1 + row2_rowid
                )
        except Exception as e:
            Log.error(f"Data: Can't swap rows. {e}")

    @classmethod
    def __get_rowid(cls, list_uid: str, uid: str) -> str:
        return cls.execute(
            "SELECT rowid FROM tasks WHERE list_uid = ? AND uid = ?",
            (list_uid, uid),
            fetch=True,
        )[0]

    @classmethod
    def execute(
        cls, cmd: str, values: tuple = (), fetch: bool = False
    ) -> list[tuple] | None:
        try:
            with cls.connection:
                cur = cls.connection.cursor()
                cur.execute(cmd, values)
                if fetch:
                    return cur.fetchall()
        except Exception as e:
            Log.error(f"Data: {e}")

    def __get_tasks_uids(cls, list_uid: str, parent: str = None) -> list[str]:
        # Log.debug(f"Data: Get tasks uids {f'for {parent}' if parent else ''}")

        with cls.connection:
            cur = cls.connection.cursor()
            cur.execute(
                f"""SELECT uid FROM tasks
                WHERE list_uid = ?
                AND deleted = 0
                {f"AND parent = '{parent}'" if parent != None else ''}""",
                (list_uid,),
            )
            return [i[0] for i in cur.fetchall()]

    def __get_sub_tasks_uids_tree(cls, list_uid: str, parent: str) -> list[str]:
        """
        Get all sub-task uids recursively
        """
        # Log.debug(f"Data: Get tasks uids tree for '{parent}'")

        uids: list[str] = []

        def _add(sub_uids: list[str]) -> None:
            for uid in sub_uids:
                uids.append(uid)
                if len(cls.__get_tasks_uids(list_uid, uid)) > 0:
                    _add(cls.__get_tasks_uids(list_uid, uid))

        _add(cls.__get_tasks_uids(list_uid, parent))

        return uids

    def __convert(cls):
        old_path = os.path.join(GLib.get_user_data_dir(), "list")
        old_data_file = os.path.join(old_path, "data.json")
        if not os.path.exists(old_data_file):
            return
        Log.debug("Data: convert data file")
        # Get tasks
        try:
            with open(old_data_file, "r") as f:
                data: dict = json.loads(f.read())
        except:
            Log.error("Data: can't read data file")
            return
        # Remove old data folder
        shutil.rmtree(old_path, True)
        # If sync is enabled
        if GSettings.get("sync-provider") != 0:
            uid = cls.add_list(GSettings.get("sync-cal-name"), synced=True)
            GSettings.set("sync-cal-name", "s", "")
        # If sync is disabled
        else:
            uid = cls.add_list("Errands")
        # Add tasks
        for task in data["tasks"]:
            cls.add_task(
                color=task["color"],
                completed=task["completed"],
                deleted=task["id"] in data["deleted"],
                list_uid=uid,
                parent=task["parent"],
                synced=task["synced_caldav"],
                text=task["text"],
                trash=task["deleted"],
                uid=task["id"],
            )


# Handle for UserData. For easily changing serialization methods.
UserData = UserDataJSON()
