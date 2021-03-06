import ast
import datetime as dt
import decimal
import inspect
import textwrap
from typing import Sequence, Any, List, Callable, NoReturn, Union, Tuple, Dict, TypedDict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.sql.type_api import TypeEngine
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.sqltypes import (
    NULLTYPE,
    BOOLEANTYPE,
    Integer,
    Float,
    Numeric,
    Interval,
    Date,
    DateTime,
    Time,
    Unicode,
    LargeBinary,
)


Type_ = Union[TypeEngine, str, Any]


class _ToSqlArgs(TypedDict):
    func: Callable[..., Any]
    argtypes: Optional[Sequence[Type_]]
    rettype: Type_


# Keep in mind: "Reflection is never clever." https://go-proverbs.github.io/
#  Unfortunately, reflection (introspection) seems like the best way
#  to ensure the code on the server and in the database remain identical.
class PlpyMan:
    def __init__(self) -> None:
        self._gd: List[Any] = []
        self._funcs: List[_ToSqlArgs] = []

    def to_gd(self, obj: Any) -> None:
        """ Registers an object to have its source copied to the PlPython Global Dictionary """
        self._gd.append(obj)

    def plpy_func(
        self,
        func: Callable[..., Any],
        argtypes: Optional[Sequence[Type_]] = None,
        rettype: Type_ = "",
    ) -> Callable[..., NoReturn]:
        """
        Decorator that registers a PlPython Function

        Functions wrapped by plpy_func do not execute on the web server.
        Instead, the function can be called from the database as a PlPython function.
        """
        self._funcs.append({"func": func, "argtypes": argtypes, "rettype": rettype})

        def wrapper(*args: Any, **kwargs: Any) -> NoReturn:
            raise TypeError(
                f"{func.__name__} was registered as a plpy_func. "
                f"This means that only the database can run this function.\n\n"
                f"Were you trying to write a function that both "
                f"this script AND Plpython could accesses?\n"
                f"Instead of decorating {func.__name__} with plpy_func, "
                f"use something like `plpy_man.to_gd({func.__name__})`. "
                f"Then you can access this function in the GD of plpy functions while "
                f"still being able to call this function normally."
            )

        return wrapper

    def flush(self, db: Session) -> None:
        """Flush registered objects
        (functions decorated by plpy_func and objects supplied to to_gd) to the database.
        """
        self._flush_gd(db)
        self._flush_funcs(db)

    def _flush_gd(self, db: Session) -> None:
        source = _prep_gd_script(self._gd)
        sql = _write_gd_sql(source)
        db.execute(sql)
        db.execute("SELECT _add_to_gd()")
        db.commit()
        self._gd = []

    def _flush_funcs(self, db: Session) -> None:
        for f in self._funcs:
            sql = _to_sql(**f)
            db.execute(sql)
        self._funcs = []


def _prep_gd_script(objs: Sequence[Callable]) -> str:
    source: List[str] = []
    for obj in objs:
        source.append(textwrap.dedent(inspect.getsource(obj)))
        source.append(f'\nGD["{obj.__name__}"] = {obj.__name__}\n\n')
    return "\n".join(source)[:-1]  # The final extraneous line is trimmed


def _write_gd_sql(py_script: str) -> text:
    return text(
        f"""\
CREATE OR REPLACE FUNCTION _add_to_gd()
RETURNS TEXT AS $$
{textwrap.indent(py_script, "    ")}
$$ LANGUAGE plpython3u;
"""
    )


class _Inspected(TypedDict):
    name: str
    args: Tuple[str, ...]
    annotations: Dict[str, Any]
    body: str


def _inspect_function(func: Callable) -> _Inspected:
    code = func.__code__
    return {
        "name": code.co_name,
        # Todo: You could mess this up with something like def why(x): z = x; del(x); return z
        "args": code.co_varnames[: code.co_argcount],
        "annotations": func.__annotations__,
        "body": _get_func_body(func),
    }


def _get_func_body(func: Callable) -> str:
    lines = inspect.getsource(func)
    source = textwrap.dedent(lines)
    _ast = ast.parse(source)
    _func_body = _ast.body[0].body  # Todo: Why doesn't mypy like this?
    segments = []
    for segment in _func_body:
        segments.append(_dedent(ast.get_source_segment(source, segment)))
    return "\n".join(segments)


# Local copy of SQLAlchemy's private type map
# Copyright (C) 2005-2021 the SQLAlchemy authors and contributors
_type_map = {
    int: Integer(),
    float: Float(),
    bool: BOOLEANTYPE,
    decimal.Decimal: Numeric(),
    dt.date: Date(),
    dt.datetime: DateTime(),
    dt.time: Time(),
    dt.timedelta: Interval(),
    type(None): NULLTYPE,
    bytes: LargeBinary(),
    str: Unicode(),
}


def _to_sql(
    func: Callable[..., Any],
    argtypes: Optional[Sequence[Type_]] = None,
    rettype: Type_ = "",
) -> text:
    class ListAppender(list):
        def __call__(self, *args: Any) -> None:
            [self.append(arg) for arg in args]

    SQL_INDENT = "  "  # 2 spaces
    PY_INDENT = "    "  # 4 spaces
    SPACE = " "  # Single Space
    NL = "\n"

    # Inspect code to get source
    func_parts = _inspect_function(func)
    name = func_parts["name"]
    args = func_parts["args"]
    annotations = func_parts["annotations"]
    body = func_parts["body"]

    name_clause = f"CREATE OR REPLACE FUNCTION {name}"

    # Argument Clause
    _argtypes: List[str] = []
    if argtypes is not None:
        for t in argtypes:
            _argtypes.append(_stringify_type(t))
    else:
        # Get arg types from type annotations
        for arg in args:
            if arg not in annotations:
                raise ValueError(
                    f"{name} is not fully type annotated. "
                    f"You can also use pass annotations types to plpy_func as SQLAlchemy Types "
                    f"or string literals."
                )
            _annotated_type = annotations[arg]
            try:
                _type = _type_map[_annotated_type]
            except KeyError as err:
                err.args = [
                    f"{_annotated_type} could not be coerced to a Postgresql type. "
                    f"Try passing the SQLAlchemy type (or a string literal) to plpy_func instead."
                ]
                raise err
            _argtypes.append(str(_type))
    args_and_types: List[Tuple[str, str]]
    args_and_types = [(name, _type) for name, _type in zip(args, _argtypes)]
    _arg_string = ", ".join(
        [(lambda _arg, _type: f"{_arg} {_type}")(*item) for item in args_and_types]
    )
    argument_clause = "".join(f"({_arg_string})")

    _return_type: str
    if rettype:
        _return_type = _stringify_type(rettype)
    else:
        # Get return type from type annotations
        if "return" not in annotations:
            raise ValueError(
                f"{name}'s return value is not type annotated. "
                f"You can also use pass annotations to plpy_func as SQLAlchemy Types "
                f"or string literals."
            )
        _annotated_type = annotations["return"]
        try:
            if _annotated_type is not None:
                _return_type = _type_map[_annotated_type]
            else:
                _return_type = ""
        except KeyError as err:
            err.args = [
                f"{_annotated_type} could not be coerced to a Postgresql type. "
                f"Try passing the SQLAlchemy type (or a string literal) to plpy_func instead."
            ]
            raise err
    return_clause = f"RETURNS {_return_type}" if _return_type else ""
    as_clause = "AS $$"
    language_clause = "$$ LANGUAGE plpython3u;"

    s = ListAppender()
    s(name_clause)
    if args:
        s(SPACE)
    s(argument_clause)
    if return_clause:
        s(NL)
        s(SQL_INDENT, return_clause, NL)
    else:
        s(SPACE)
    s(as_clause, NL)
    s(textwrap.indent(body, PY_INDENT), NL)
    s(language_clause, NL)

    return text("".join(s))


def _stringify_type(_type: Type_) -> str:
    if callable(_type):
        return str(_type())
    return str(_type)


def _dedent(string: str) -> str:
    lines = []
    for line in string.splitlines():
        if line.startswith("        "):
            line = line[4:]
        lines.append(line)
    return "\n".join(lines)
