import logging
import textwrap

import pytest
from sqlalchemy import text

import plpy_man
from plpy_man import _to_sql, plpy


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_db_setup(db) -> None:
    actual = db.execute(text("SELECT 1")).one()
    expected = (1,)
    assert actual == expected


class TestAddToGD:
    def test_prep_source(self) -> None:
        def func(x="hello", y="world"):
            """ This is a docstring. """
            # This is a comment
            return f"{x}, {y}"

        class Class:
            pass

        actual = plpy_man._prep_gd_script([func, Class])
        expected = textwrap.dedent(
            '''\
            def func(x="hello", y="world"):
                """ This is a docstring. """
                # This is a comment
                return f"{x}, {y}"


            GD["func"] = func


            class Class:
                pass


            GD["Class"] = Class
            '''
        )
        assert actual == expected

    def test_prep_source_with_alias(self):
        # Todo: Rename test
        def original():
            pass

        new1 = original
        actual1 = plpy_man._prep_gd_script([new1])
        expected = textwrap.dedent(
            """\
            def original():
                pass


            GD["original"] = original
            """
        )
        assert actual1 == expected
        # Todo: Change to inspect to make this work
        original.__name__ = "new name"
        actual2 = plpy_man._prep_gd_script([new1])
        assert actual2 == expected

    #     def test_prep_source_with_new_name(self):
    #         def original():
    #             pass
    #
    #         original.__name__ = "new"
    #
    #         actual = plpy_man._prep_source([original])
    #         expected = """\
    # def original():
    #     pass
    #
    #
    # GD["new"] = new
    # """
    #         assert actual == expected

    def test_write_sql(self) -> None:
        py_script = textwrap.dedent(
            '''\
            def func(x="hello", y="world"):
                """ This is a docstring. """
                # This is a comment
                return f"{x}, {y}"


            GD["func"] = func


            class Class:
                pass


            GD["Class"] = Class
            '''
        )
        result = plpy_man._write_gd_sql(py_script)
        expected_str = textwrap.dedent(
            '''\
            CREATE OR REPLACE FUNCTION _add_to_gd()
            RETURNS TEXT AS $$
            def func(x="hello", y="world"):
                """ This is a docstring. """
                # This is a comment
                return f"{x}, {y}"


            GD["func"] = func


            class Class:
                pass


            GD["Class"] = Class

            $$ LANGUAGE plpython3u;
            '''
        )
        assert result != expected_str
        assert str(result) == expected_str

    # def test_upload_to_postgres(self, db):
    #     # Todo: unit test the last part of the function? Seems unnecessary
    #     pass

    # def test_method_with_default_manager(self, db):
    #     manager = plpy_man.PlpyMan()
    #
    #     def hello_world():
    #         return "Hello, world"
    #
    #     manager.to_gd(hello_world)
    #     manager.flush(db)
    #     actual = db.execute(text("SELECT hello_world()")).first()
    #     expected = ("Hello, world",)
    #     assert actual == expected


# fmt: off
########################################################################################
# # https://www.postgresql.org/docs/13/plpython-data.html#id-1.8.11.11.3
# def test_plpython_called():
#     # boolean -> bool
#     # smallint, bigint, oid, int -> int
#     # real, double -> float
#     # numeric -> Decimal
#     # bytea -> bytes
#     # Everything else -> str
#     # Nonscalar -> Other?
#
#
# def test_plpython_returns():
#     # boolean -> bool(result)
#     # bytea -> bytes(result) # And then to bytea
#     # everything else -> str(result) # And then to type # float uses __repr__ instead
#     # Nonscalar -> other


def test_type_annotations() -> None:
    def pyadd(a: int, b: int) -> None:
        print(a + b)

    actual = _to_sql(pyadd).__str__()
    expected = """\
CREATE OR REPLACE FUNCTION pyadd (a INTEGER, b INTEGER) AS $$
    print(a + b)
$$ LANGUAGE plpython3u;
"""
    assert actual == expected


########################################################################################
# https://www.postgresql.org/docs/13/plpython-data.html#id-1.8.11.11.4
def test_null_none():
    def pymax(a, b):
        if (a is None) or (b is None):
            return None
        if a > b:
            return a
        return b

    actual = _to_sql(pymax, argtypes=["integer", "integer"], rettype="integer").__str__()
    expected = (
        """\
CREATE OR REPLACE FUNCTION pymax (a integer, b integer)
  RETURNS integer
AS $$
    if (a is None) or (b is None):
        return None
    if a > b:
        return a
    return b
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


########################################################################################
# https://www.postgresql.org/docs/13/plpython-data.html#PLPYTHON-ARRAYS
def test_arrays_lists():
    def return_arr():
        return [1, 2, 3, 4, 5]

    actual = _to_sql(return_arr, argtypes=[], rettype="int[]").__str__()
    expected = (
        """\
CREATE OR REPLACE FUNCTION return_arr()
  RETURNS int[]
AS $$
    return [1, 2, 3, 4, 5]
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


def test_multi_dimensional_array():
    def test_type_conversion_array_int4(x):
        plpy.info(x, type(x))
        return x

    actual = _to_sql(
        test_type_conversion_array_int4,
        argtypes=["int4[]"],
        rettype="int4[]"
    ).__str__()
    expected = (
        """\
CREATE OR REPLACE FUNCTION test_type_conversion_array_int4 (x int4[])
  RETURNS int4[]
AS $$
    plpy.info(x, type(x))
    return x
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


def test_new_to_python_and_strings():
    def return_str_arr():
        return "hello"

    actual = _to_sql(return_str_arr, rettype="varchar[]").__str__()
    expected = (
        """\
CREATE OR REPLACE FUNCTION return_str_arr()
  RETURNS varchar[]
AS $$
    return "hello"
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


########################################################################################
# https://www.postgresql.org/docs/13/plpython-data.html#id-1.8.11.11.6
def test_composite_types():
    def overpaid(e):
        if e["salary"] > 200000:
            return True
        if (e["age"] < 30) and (e["salary"] > 100000):
            return True
        return False

    actual = _to_sql(overpaid, argtypes=["employee"], rettype="boolean").__str__()
    expected = (
        """\
CREATE OR REPLACE FUNCTION overpaid (e employee)
  RETURNS boolean
AS $$
    if e["salary"] > 200000:
        return True
    if (e["age"] < 30) and (e["salary"] > 100000):
        return True
    return False
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


def test_named_pair_sequence():
    def make_pair(name, value):
        return ( name, value )
    actual = _to_sql(make_pair, argtypes=["text", "integer"], rettype="named_value").__str__()
    expected = (
        """\
CREATE OR REPLACE FUNCTION make_pair (name text, value integer)
  RETURNS named_value
AS $$
    return ( name, value )
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


def test_comments():
    def make_pair(name, value):
        ''' docstring is included '''
        # Comments are parsed out
        return ( name, value )
        # or alternatively, as tuple: return [ name, value ]

    actual = _to_sql(make_pair, argtypes=["text", "integer"], rettype="named_value").__str__()
    expected = (
        """\
CREATE OR REPLACE FUNCTION make_pair (name text, value integer)
  RETURNS named_value
AS $$
    ''' docstring is included '''
    return ( name, value )
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


def test_named_pair_mapping():
    def make_pair(name, value):
        return { "name": name, "value": value }

    actual = _to_sql(make_pair, argtypes=["text", "integer"], rettype="named_value").__str__()
    expected = ("""\
CREATE OR REPLACE FUNCTION make_pair (name text, value integer)
  RETURNS named_value
AS $$
    return { "name": name, "value": value }
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


def test_after_return():
    def make_pair(name, value):
        class named_value:
            def __init__(self, n, v):
                self.name = n
                self.value = v
        return named_value(name, value)

        # or simply
        class nv: pass
        nv.name = name
        nv.value = value
        return nv

    actual = _to_sql(make_pair, argtypes=["text", "integer"], rettype="named_value").__str__()
    expected = ("""\
CREATE OR REPLACE FUNCTION make_pair (name text, value integer)
  RETURNS named_value
AS $$
    class named_value:
        def __init__(self, n, v):
            self.name = n
            self.value = v
    return named_value(name, value)
    class nv: pass
    nv.name = name
    nv.value = value
    return nv
$$ LANGUAGE plpython3u;
"""
    )
    assert actual == expected


# def test_function_with_out_parameters():
#     def multiout_simple(i, j):
#         return (1, 2)
#
#     actual = to_sql(multiout_simple).__str__()
#     expected = ("""\
# CREATE OR REPLACE FUNCTION multiout_simple(OUT i integer, OUT j integer) AS $$
#     return (1, 2)
# $$ LANGUAGE plpython3u;
# """
#     )
#     assert actual == expected
#
#
# def test_procedure_with_output_parameters():
#     def python_triple(a, b):
#         return (a * 3, b * 3)
#
#     actual = to_sql(python_triple).__str__()
#     expected = ("""\
# CREATE PROCEDURE python_triple(INOUT a integer, INOUT b integer) AS $$
#     return (a * 3, b * 3)
# $$ LANGUAGE plpython3u;
# """
#     )
#     assert actual == expected
#
#
# ########################################################################################
# # https://www.postgresql.org/docs/13/plpython-data.html#id-1.8.11.11.7
# def test_set_from_sequence():
#     def greet(how):
#         # return tuple containing lists as composite types
#         # all other combinations work also
#         return ( [ how, "World" ], [ how, "PostgreSQL" ], [ how, "PL/Python" ] )
#     actual = to_sql(greet, argtypes=["text"]).__str__()
#     expected = ("""\
# CREATE OR REPLACE FUNCTION greet (how text)
#   RETURNS SETOF greeting
# AS $$
#     # return tuple containing lists as composite types
#     # all other combinations work also
#     return ( [ how, "World" ], [ how, "PostgreSQL" ], [ how, "PL/Python" ] )
# $$ LANGUAGE plpython3u
# """
#     )
#     assert actual == expected
#
#
# def test_set_from_iterator():
#     def greet(how):
#         class producer:
#             def __init__(self, how, who):
#                 self.how = how
#                 self.who = who
#                 self.ndx = -1
#
#             def __iter__(self):
#                 return self
#
#             def next(self):
#                 self.ndx += 1
#                 if self.ndx == len(self.who):
#                     raise StopIteration
#                 return (self.how, self.who[self.ndx])
#
#         return producer(how, [ "World", "PostgreSQL", "PL/Python" ])
#
#     actual = to_sql(greet, argtypes=["text"]).__str__()
#     expected = ("""\
# CREATE OR REPLACE FUNCTION greet (how text)
#   RETURNS SETOF greeting
# AS $$
#     class producer:
#         def __init__ (self, how, who):
#           self.how = how
#           self.who = who
#           self.ndx = -1
#
#         def __iter__ (self):
#           return self
#
#         def next (self):
#           self.ndx += 1
#           if self.ndx == len(self.who):
#             raise StopIteration
#           return ( self.how, self.who[self.ndx] )
#
#     return producer(how, [ "World", "PostgreSQL", "PL/Python" ])
# $$ LANGUAGE plpython3u;
# """
#     )
#     assert actual == expected
#
#
# def test_set_from_generator():
#     def greet(how):
#         for who in ["World", "PostgreSQL", "PL/Python"]:
#             yield ( how, who )
#     actual = to_sql(greet, ["text"]).__str__()
#     expected = ("""\
# CREATE OR REPLACE FUNCTION greet (how text)
#   RETURNS SETOF greeting
# AS $$
#     for who in [ "World", "PostgreSQL", "PL/Python" ]:
#         yield ( how, who )
# $$ LANGUAGE plpython3u;
# """
#     )
#     assert actual == expected
#
#
# def test_set_returning_function_with_OUT_parameters():
#     def multiout_simple_setof(n):
#         return [(1, 2)] * n
#     actual = to_sql(multiout_simple_setof).__str__()
#     expected = ("""\
# CREATE OR REPLACE FUNCTION multiout_simple_setof(n integer, OUT integer, OUT integer)
#   RETURNS SETOF record
# AS $$
#     return [(1, 2)] * n
# $$ LANGUAGE plpython3u;
# """
#     )
#     assert actual == expected


# fmt: on
if __name__ == "__main__":
    pytest.main()
