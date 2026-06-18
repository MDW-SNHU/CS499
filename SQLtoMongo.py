# SQLtoMongo.py
#
# SQLToMongoTranslator
#
# Executes SQL-style statements against MongoDB via MongoManager.
# Supports:
#   - SELECT with WHERE, ORDER BY, LIMIT, OFFSET
#   - Nested SELECT in FROM (derived tables)
#   - Aggregations: COUNT, SUM, AVG, MIN, MAX
#   - INSERT, UPDATE, DELETE
#
# Execution model:
#   - Simple SELECTs run against the current active database (mongo.mm_database).
#   - Nested SELECTs / derived tables use a temporary MongoDB database:
#       _sql_tmp_YYYYMMDD_HHMMSS_<uuid>
#   - Derived tables are materialized into temporary collections:
#       tmp_select_<uuid>
#   - Original DB is preserved and restored after execution.
#
# Read-only behavior:
#   - Read-only users can run simple SELECTs and aggregations.
#   - Operations requiring writes (INSERT/UPDATE/DELETE, nested SELECTs with temp DBs)
#     are degraded gracefully with warnings and empty results.

# SQLtoMongo.py
#
# Bottom-up SQL-to-Mongo engine with hybrid temp DB / temp collection handling.
# - Evaluates nested SELECTs from the inside out.
# - Materializes subquery results into temp collections (in temp DB or active DB).
# - Supports COUNT/SUM/AVG/MIN/MAX, WHERE, ORDER BY, LIMIT, OFFSET.
# - Detects read-only users and degrades nested features gracefully.
# - Uses temp DB when DB-level write privileges exist; otherwise temp collections
#   in the active DB (if write allowed there).
# - Includes Option C privilege detection: if privilege metadata is missing,
#   probe DB-level write, then collection-level write, then fallback to read-only.
# - Logging controlled by environment variable SQL2MONGO_LOGLEVEL:
#       NONE  = no logging
#       LIGHT = high-level logging
#       DEBUG = deep internal logging

from ast import stmt
import os
import re
import uuid
from datetime import datetime
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, List, Optional, Dict

from bson import ObjectId


# ============================================================
# LOGGING HELPERS
# ============================================================

LOGLEVEL = os.getenv("SQL2MONGO_LOGLEVEL", "NONE").upper()

def _log_light(msg: str):
    if LOGLEVEL in ("LIGHT", "DEBUG"):
        print(f"[SQL2MONGO] {msg}")

def _log_debug(msg: str):
    if LOGLEVEL == "DEBUG":
        print(f"[SQL2MONGO DEBUG] {msg}")


# ============================================================
# TOKENIZER
# ============================================================

class TokenType(Enum):
    IDENT = auto()
    STRING = auto()
    NUMBER = auto()
    COMMA = auto()
    DOT = auto()
    LPAREN = auto()
    RPAREN = auto()
    STAR = auto()
    EQ = auto()
    LT = auto()
    LTE = auto()
    GT = auto()
    GTE = auto()
    KEYWORD = auto()
    EOF = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    position: int


# ============================================================
# AST NODES
# ============================================================

@dataclass
class SelectStmt:
    fields: List[Any]
    from_source: Any
    where: Optional[Any]
    order_by: Optional[List[tuple]]
    limit: Optional[int]
    offset: Optional[int]
    distinct: bool


@dataclass
class TableRef:
    name: str


@dataclass
class SubqueryRef:
    select: SelectStmt


@dataclass
class InsertStmt:
    table: str
    columns: Optional[List[str]]
    values: List[Any]


@dataclass
class UpdateSetItem:
    column: str
    value: Any


@dataclass
class UpdateStmt:
    table: str
    set_items: List[UpdateSetItem]
    where: Optional[Any]


@dataclass
class DeleteStmt:
    table: str
    where: Optional[Any]


@dataclass
class BinaryExpr:
    op: str
    left: Any
    right: Any


@dataclass
class LikeExpr:
    field: str
    pattern: str


@dataclass
class CompareExpr:
    field: str
    op: str
    value: Any


@dataclass
class InSubqueryExpr:
    field: str
    subquery: SelectStmt

@dataclass
class DescribeStmt:
    table: str

@dataclass
class HelpStmt:
    topic: Optional[str]  # e.g., SELECT, INSERT, or None for general help

@dataclass
class DropTableStmt:
    table: str

@dataclass
class ShowTablesStmt:
    pass

@dataclass
class ShowDatabasesStmt:
    pass

@dataclass
class ShowIndexesStmt:
    table: str

# ============================================================
# LEXER
# ============================================================

class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def _peek(self) -> str:
        if self.pos < len(self.text):
            return self.text[self.pos]
        return ""

    def _advance(self) -> str:
        ch = self._peek()
        self.pos += 1
        return ch

    def tokens(self) -> List[Token]:
        toks: List[Token] = []
        while self.pos < len(self.text):
            ch = self._peek()
            if ch.isspace():
                self._advance()
                continue
            start = self.pos

            # Identifiers / keywords
            if ch.isalpha() or ch == "_":
                ident = ""
                while self._peek().isalnum() or self._peek() in ("_", "."):
                    ident += self._advance()
                upper = ident.upper()
                if upper in [
                    "SELECT", "FROM", "WHERE", "ORDER", "BY", "LIMIT", "OFFSET",
                    "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
                    "DISTINCT", "AND", "OR", "LIKE", "IN", "ASC", "DESC",
                    "DESCRIBE", "HELP", "DROP", "TABLE",
                    "SHOW", "TABLES", "DATABASES", "INDEXES", "FROM"
                ]:
                    toks.append(Token(TokenType.KEYWORD, upper, start))
                else:
                    toks.append(Token(TokenType.IDENT, ident, start))
                continue

            # Numbers
            if ch.isdigit():
                num = ""
                while self._peek().isdigit():
                    num += self._advance()
                toks.append(Token(TokenType.NUMBER, num, start))
                continue

            # Strings (single or double quoted)
            if ch in ("'", '"'):
                quote = self._advance()
                val = ""
                while self._peek() and self._peek() != quote:
                    val += self._advance()
                if self._peek() == quote:
                    self._advance()
                toks.append(Token(TokenType.STRING, val, start))
                continue

            # Single-character tokens
            if ch == ",":
                self._advance()
                toks.append(Token(TokenType.COMMA, ",", start))
                continue
            if ch == "(":
                self._advance()
                toks.append(Token(TokenType.LPAREN, "(", start))
                continue
            if ch == ")":
                self._advance()
                toks.append(Token(TokenType.RPAREN, ")", start))
                continue
            if ch == "*":
                self._advance()
                toks.append(Token(TokenType.STAR, "*", start))
                continue
            if ch == "=":
                self._advance()
                toks.append(Token(TokenType.EQ, "=", start))
                continue
            if ch == "<":
                self._advance()
                if self._peek() == "=":
                    self._advance()
                    toks.append(Token(TokenType.LTE, "<=", start))
                else:
                    toks.append(Token(TokenType.LT, "<", start))
                continue
            if ch == ">":
                self._advance()
                if self._peek() == "=":
                    self._advance()
                    toks.append(Token(TokenType.GTE, ">=", start))
                else:
                    toks.append(Token(TokenType.GT, ">", start))
                continue

            # Unknown char
            self._advance()

        toks.append(Token(TokenType.EOF, "", self.pos))
        return toks

# ============================================================
# PARSER
# ============================================================

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.index = 0

    def current(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        tok = self.current()
        self.index += 1
        return tok

    def expect_kw(self, kw: str) -> Token:
        tok = self.current()
        if tok.type != TokenType.KEYWORD or tok.value != kw:
            raise Exception(f"Expected keyword {kw}, got {tok.value}")
        return self.advance()

    def expect(self, t: TokenType) -> Token:
        tok = self.current()
        if tok.type != t:
            raise Exception(f"Expected {t}, got {tok.type}")
        return self.advance()

    def parse_statement(self) -> Any:
        tok = self.current()
        if tok.type == TokenType.KEYWORD:
            if tok.value == "SELECT":
                return self.parse_select()
            if tok.value == "INSERT":
                return self.parse_insert()
            if tok.value == "UPDATE":
                return self.parse_update()
            if tok.value == "DELETE":
                return self.parse_delete()
            if tok.value == "DROP":
               return self.parse_drop_table()
            if tok.value == "SHOW":
                return self.parse_show()
            if tok.value == "DESCRIBE":
                return self.parse_describe()
            if tok.value == "HELP":
                return self.parse_help()
        raise Exception(f"Unsupported SQL command: {tok.value}")

    # ------------------------------------------------------------
    # SELECT
    # ------------------------------------------------------------
    def parse_select(self) -> SelectStmt:
        self.expect_kw("SELECT")
        distinct = False
        if self.current().type == TokenType.KEYWORD and self.current().value == "DISTINCT":
            distinct = True
            self.advance()

        fields = self.parse_select_list()

        self.expect_kw("FROM")
        from_source = self.parse_from_source()

        where = None
        order_by = None
        limit = None
        offset = None

        while self.current().type == TokenType.KEYWORD:
            kw = self.current().value
            if kw == "WHERE":
                self.advance()
                where = self.parse_expr()
            elif kw == "ORDER":
                self.advance()
                self.expect_kw("BY")
                order_by = self.parse_order_by()
            elif kw == "LIMIT":
                self.advance()
                if self.current().type == TokenType.NUMBER:
                    limit = int(self.advance().value)
            elif kw == "OFFSET":
                self.advance()
                if self.current().type == TokenType.NUMBER:
                    offset = int(self.advance().value)
            else:
                break

        return SelectStmt(
            fields=fields,
            from_source=from_source,
            where=where,
            order_by=order_by,
            limit=limit,
            offset=offset,
            distinct=distinct
        )

    def parse_select_list(self) -> List[Any]:
        items = [self.parse_select_item()]
        while self.current().type == TokenType.COMMA:
            self.advance()
            items.append(self.parse_select_item())
        return items

    def parse_select_item(self) -> Any:
        tok = self.current()

        if tok.type == TokenType.STAR:
            self.advance()
            return "*"

        if tok.type == TokenType.IDENT:
            ident = self.advance().value
            if self.current().type == TokenType.LPAREN:
                self.advance()
                args: List[Any] = []
                if self.current().type != TokenType.RPAREN:
                    if self.current().type == TokenType.STAR:
                        self.advance()
                        args.append("*")
                    else:
                        args.append(self.parse_value())

                    while self.current().type == TokenType.COMMA:
                        self.advance()
                        if self.current().type == TokenType.STAR:
                            self.advance()
                            args.append("*")
                        else:
                            args.append(self.parse_value())
                self.expect(TokenType.RPAREN)
                arg_sql_parts = []
                for a in args:
                    arg_sql_parts.append(self._value_to_sql(a))
                arg_str = ",".join(arg_sql_parts)
                return f"{ident}({arg_str})"
            return ident

        if tok.type == TokenType.LPAREN:
            self.advance()
            sub = self.parse_select()
            self.expect(TokenType.RPAREN)
            return sub

        raise Exception(f"Invalid SELECT item starting at {tok.value}")

    def parse_from_source(self) -> Any:
        tok = self.current()
        if tok.type == TokenType.LPAREN:
            self.advance()
            sub_select = self.parse_select()
            self.expect(TokenType.RPAREN)
            return SubqueryRef(select=sub_select)
        if tok.type == TokenType.IDENT:
            return TableRef(name=self.advance().value)
        raise Exception(f"Invalid FROM source: {tok.value}")

    def parse_order_by(self) -> List[tuple]:
        items: List[tuple] = []
        field = self.expect(TokenType.IDENT).value
        direction = 1
        if self.current().type == TokenType.KEYWORD and self.current().value in ("ASC", "DESC"):
            if self.current().value == "DESC":
                direction = -1
            self.advance()
        items.append((field, direction))
        while self.current().type == TokenType.COMMA:
            self.advance()
            field = self.expect(TokenType.IDENT).value
            direction = 1
            if self.current().type == TokenType.KEYWORD and self.current().value in ("ASC", "DESC"):
                if self.current().value == "DESC":
                    direction = -1
                self.advance()
            items.append((field, direction))
        return items

    # ------------------------------------------------------------
    # WHERE expressions
    # ------------------------------------------------------------
    def parse_expr(self) -> Any:
        return self.parse_or()

    def parse_or(self) -> Any:
        left = self.parse_and()
        while self.current().type == TokenType.KEYWORD and self.current().value == "OR":
            op = self.advance().value
            right = self.parse_and()
            left = BinaryExpr(op=op, left=left, right=right)
        return left

    def parse_and(self) -> Any:
        left = self.parse_primary_cond()
        while self.current().type == TokenType.KEYWORD and self.current().value == "AND":
            op = self.advance().value
            right = self.parse_primary_cond()
            left = BinaryExpr(op=op, left=left, right=right)
        return left

    def parse_primary_cond(self) -> Any:
        tok = self.current()
        if tok.type == TokenType.IDENT:
            field = self.advance().value
            if self.current().type == TokenType.KEYWORD and self.current().value == "LIKE":
                self.advance()
                val_tok = self.current()
                if val_tok.type == TokenType.STRING:
                    pattern = self.advance().value
                else:
                    raise Exception("LIKE requires string literal")
                return LikeExpr(field=field, pattern=pattern)
            if self.current().type == TokenType.KEYWORD and self.current().value == "IN":
                self.advance()
                self.expect(TokenType.LPAREN)
                sub_select = self.parse_select()
                self.expect(TokenType.RPAREN)
                return InSubqueryExpr(field=field, subquery=sub_select)
            op_tok = self.current()
            if op_tok.type in (TokenType.EQ, TokenType.LT, TokenType.LTE, TokenType.GT, TokenType.GTE):
                op = self.advance().value
                val = self.parse_value()
                return CompareExpr(field=field, op=op, value=val)
        if tok.type == TokenType.LPAREN:
            self.advance()
            inner = self.parse_expr()
            self.expect(TokenType.RPAREN)
            return inner
        raise Exception(f"Unsupported condition starting at {tok.value}")

    def parse_value(self) -> Any:
        tok = self.current()
        if tok.type == TokenType.STRING:
            return self.advance().value
        if tok.type == TokenType.NUMBER:
            return int(self.advance().value)
        if tok.type == TokenType.IDENT:
            return self.advance().value
        raise Exception(f"Unsupported value token: {tok.value}")

    def _value_to_sql(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        return str(value)

    # ------------------------------------------------------------
    # INSERT
    # ------------------------------------------------------------
    def parse_insert(self) -> InsertStmt:
        self.expect_kw("INSERT")
        self.expect_kw("INTO")
        table = self.expect(TokenType.IDENT).value

        columns: Optional[List[str]] = None
        if self.current().type == TokenType.LPAREN:
            self.advance()
            columns = []
            columns.append(self.expect(TokenType.IDENT).value)
            while self.current().type == TokenType.COMMA:
                self.advance()
                columns.append(self.expect(TokenType.IDENT).value)
            self.expect(TokenType.RPAREN)

        self.expect_kw("VALUES")
        self.expect(TokenType.LPAREN)
        values: List[Any] = []
        values.append(self.parse_value())
        while self.current().type == TokenType.COMMA:
            self.advance()
            values.append(self.parse_value())
        self.expect(TokenType.RPAREN)

        return InsertStmt(table=table, columns=columns, values=values)

    # ------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------
    def parse_update(self) -> UpdateStmt:
        self.expect_kw("UPDATE")
        table = self.expect(TokenType.IDENT).value
        self.expect_kw("SET")
        set_items: List[UpdateSetItem] = []
        set_items.append(self.parse_set_item())
        while self.current().type == TokenType.COMMA:
            self.advance()
            set_items.append(self.parse_set_item())

        where = None
        if self.current().type == TokenType.KEYWORD and self.current().value == "WHERE":
            self.advance()
            where = self.parse_expr()

        return UpdateStmt(table=table, set_items=set_items, where=where)

    def parse_set_item(self) -> UpdateSetItem:
        col = self.expect(TokenType.IDENT).value
        self.expect(TokenType.EQ)
        val = self.parse_value()
        return UpdateSetItem(column=col, value=val)

    # ------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------
    def parse_delete(self) -> DeleteStmt:
        self.expect_kw("DELETE")
        self.expect_kw("FROM")
        table = self.expect(TokenType.IDENT).value
        where = None
        if self.current().type == TokenType.KEYWORD and self.current().value == "WHERE":
            self.advance()
            where = self.parse_expr()
        return DeleteStmt(table=table, where=where)
    
    # ------------------------------------------------------------
    # DROP TABLE
    # ------------------------------------------------------------
    def parse_drop_table(self) -> DropTableStmt:
        self.expect_kw("DROP")
        self.expect_kw("TABLE")
        table = self.expect(TokenType.IDENT).value
        return DropTableStmt(table=table)
    
    # ------------------------------------------------------------
    # SHOW
    # ------------------------------------------------------------ 
    def parse_show(self):
        self.expect_kw("SHOW")

        # SHOW TABLES
        if self.current().type == TokenType.KEYWORD and self.current().value == "TABLES":
            self.advance()
            return ShowTablesStmt()

        # SHOW DATABASES
        if self.current().type == TokenType.KEYWORD and self.current().value == "DATABASES":
            self.advance()
            return ShowDatabasesStmt()

        # SHOW INDEXES FROM <table>
        if self.current().type == TokenType.KEYWORD and self.current().value == "INDEXES":
            self.advance()
            self.expect_kw("FROM")
            table = self.expect(TokenType.IDENT).value
            return ShowIndexesStmt(table=table)

        return "Unsupported SHOW command."
    
    # ------------------------------------------------------------
    # DESCRIBE
    # ------------------------------------------------------------
    def parse_describe(self) -> str:
        self.expect_kw("DESCRIBE")
        table = self.expect(TokenType.IDENT).value
        return DescribeStmt(table=table)
    
    # ------------------------------------------------------------
    # HELP
    # ------------------------------------------------------------
    def parse_help(self) -> HelpStmt:
        self.expect_kw("HELP")
        topic = None
        if (self.current().type == TokenType.KEYWORD and self.current().value not in ("EOF")):
            topic = self.advance().value
        return HelpStmt(topic=topic)
# ============================================================
# SQL ENGINE
# ============================================================

class SQLToMongoTranslator:
    def __init__(self, mongo_manager: Any):
        self.mongo = mongo_manager

        self.agg_functions = {"COUNT", "SUM", "AVG", "MIN", "MAX"}

        self.sql_keywords = [
            "SELECT", "FROM", "WHERE", "ORDER", "BY",
            "LIMIT", "OFFSET", "INSERT", "INTO", "VALUES",
            "UPDATE", "SET", "DELETE", "DISTINCT",
            "AND", "OR", "LIKE", "IN", "ASC", "DESC",
            "DESCRIBE"
        ]

        self.read_only = False
        self.has_db_write = False
        self.warnings: List[str] = []
        self._temp_db_name: Optional[str] = None

        self._detect_privileges()

    # ------------------------------------------------------------
    # OPTION C PRIVILEGE DETECTION
    # ------------------------------------------------------------
    def _detect_privileges(self):
        """
        Option C:
        1. Try connectionStatus (if available)
        2. If that fails, try creating/dropping a dummy DB
        3. If that fails, try creating/dropping a dummy collection
        4. If that fails, user is read-only
        """

        db = getattr(self.mongo, "mm_database", None)
        if db is None:
            self.read_only = True
            self.has_db_write = False
            return

        # STEP 1 — Try connectionStatus
        try:
            _log_debug("Attempting connectionStatus privilege detection...")
            status = db.command("connectionStatus")
            privileges = status["authInfo"]["authenticatedUserPrivileges"]

            write_actions = {
                "insert", "update", "remove",
                "createCollection", "dropCollection"
            }
            db_write_actions = {
                "createDatabase", "dropDatabase"
            }

            has_write = False
            has_db_write = False

            for priv in privileges:
                actions = set(priv.get("actions", []))
                if actions & write_actions:
                    has_write = True
                if actions & db_write_actions:
                    has_db_write = True

            self.read_only = not has_write
            self.has_db_write = has_db_write

            _log_debug(f"connectionStatus: read_only={self.read_only}, db_write={self.has_db_write}")
            return

        except Exception as e:
            _log_debug(f"connectionStatus failed: {e}")

        # STEP 2 — Probe DB-level write
        try:
            dummy_db = f"__dummydb_{uuid.uuid4().hex}__"
            _log_debug(f"Probing DB-level write using temp DB: {dummy_db}")

            self.mongo.client[dummy_db].create_collection("__probe__")
            self.mongo.client.drop_database(dummy_db)

            self.read_only = False
            self.has_db_write = True

            _log_debug("DB-level write probe succeeded.")
            return

        except Exception as e:
            _log_debug(f"DB-level write probe failed: {e}")

        # STEP 3 — Probe collection-level write
        try:
            _log_debug("Probing collection-level write in active DB...")
            db.create_collection("__probe__")
            db.drop_collection("__probe__")

            self.read_only = False
            self.has_db_write = False

            _log_debug("Collection-level write probe succeeded.")
            return

        except Exception as e:
            _log_debug(f"Collection-level write probe failed: {e}")

        # STEP 4 — Fully read-only
        self.read_only = True
        self.has_db_write = False
        _log_debug("User is fully read-only.")

    # ------------------------------------------------------------
    # SQL ENTRY POINT
    # ------------------------------------------------------------
    def translate_sql(self, sql: str) -> Dict[str, Any]:
        sql = sql.strip()
        if sql.endswith(";"):
            sql = sql[:-1]

        lexer = Lexer(sql)
        tokens = lexer.tokens()
        parser = Parser(tokens)
        stmt = parser.parse_statement()

        if isinstance(stmt, SelectStmt):
            return self._execute_select(stmt)
        if isinstance(stmt, InsertStmt):
            return self._execute_insert(stmt)
        if isinstance(stmt, UpdateStmt):
            return self._execute_update(stmt)
        if isinstance(stmt, DeleteStmt):
            return self._execute_delete(stmt)
        if isinstance(stmt, DropTableStmt):
            return self._execute_drop_table(stmt)
        if isinstance(stmt, ShowTablesStmt):
            return self._execute_show_tables()
        if isinstance(stmt, ShowDatabasesStmt):
            return self._execute_show_databases()
        if isinstance(stmt, ShowIndexesStmt):
            return self._execute_show_indexes(stmt)
        if isinstance(stmt, DescribeStmt):
            return self._execute_describe(stmt)
        if isinstance(stmt, HelpStmt):
            return self._execute_help(stmt)

        raise Exception("Unsupported SQL statement type.")

    # ------------------------------------------------------------
    # SELECT EXECUTION (TOP LEVEL)
    # ------------------------------------------------------------
    def _execute_select(self, stmt: SelectStmt) -> Dict[str, Any]:
        _log_light("Executing SELECT statement")

        plan_lines: List[str] = []
        result = self._eval_select(stmt, plan_lines)

        return {
            "sql": self._reconstruct_sql(stmt),
            "mongo_plan": "\n".join(plan_lines),
            "result": result
        }

    # ------------------------------------------------------------
    # BOTTOM-UP SELECT EVALUATION
    # ------------------------------------------------------------
    def _eval_select(self, stmt: SelectStmt, plan: List[str]) -> List[Dict[str, Any]]:
        """
        Evaluate SELECT bottom-up.
        If FROM is a subquery, evaluate it first and materialize into a temp collection.
        """

        # 1. Resolve FROM source
        if isinstance(stmt.from_source, TableRef):
            coll_name = stmt.from_source.name
            plan.append(f"FROM TABLE: {coll_name}")
            _log_debug(f"Reading from collection '{coll_name}'")
            base_coll = self.mongo.mm_database[coll_name]

        elif isinstance(stmt.from_source, SubqueryRef):
            plan.append("FROM SUBQUERY → materializing")
            _log_light("Materializing subquery")

            temp_name = self._materialize_subquery(stmt.from_source.select, plan)
            base_coll = self.mongo.mm_database[temp_name]
            plan.append(f"SUBQUERY MATERIALIZED AS: {temp_name}")

        else:
            raise Exception("Invalid FROM source")

        # 2. WHERE
        mongo_filter = {}
        if stmt.where:
            mongo_filter = self._compile_where(stmt.where, plan)
            plan.append(f"WHERE FILTER: {mongo_filter}")

        # 3. ORDER BY
        sort_spec = None
        if stmt.order_by:
            sort_spec = []
            for field, direction in stmt.order_by:
                sort_spec.append((field, direction))
            plan.append(f"ORDER BY: {sort_spec}")

        # 4. LIMIT / OFFSET
        limit = stmt.limit
        offset = stmt.offset
        if limit is not None:
            plan.append(f"LIMIT: {limit}")
        if offset is not None:
            plan.append(f"OFFSET: {offset}")

        # 5. Execute Mongo query
        cursor = base_coll.find(mongo_filter)

        if sort_spec:
            cursor = cursor.sort(sort_spec)
        if offset:
            cursor = cursor.skip(offset)
        if limit:
            cursor = cursor.limit(limit)

        docs = []
        for d in cursor:
            docs.append(d)

        # 6. DISTINCT (apply AFTER projection)
        if stmt.distinct:
            plan.append("APPLY DISTINCT")

            # First apply projection so we only dedupe on selected fields
            projected = self._apply_projection(stmt.fields, docs, plan)

            seen = set()
            unique_docs = []

            for row in projected:
                # Convert lists → tuples so they become hashable
                def make_hashable(value):
                    if isinstance(value, list):
                        converted_list = []
                        for v in value:
                            converted_list.append(make_hashable(v))
                        return tuple(converted_list)
                    if isinstance(value, dict):
                        pairs = []
                        for k in value:
                            pairs.append((k, make_hashable(value[k])))
                        for i in range(len(pairs)):
                            for j in range(i + 1, len(pairs)):
                                if pairs[j][0] < pairs[i][0]:
                                    temp_pair = pairs[i]
                                    pairs[i] = pairs[j]
                                    pairs[j] = temp_pair
                        return tuple(pairs)
                    return value

                row_pairs = []
                for k in row:
                    row_pairs.append((k, make_hashable(row[k])))
                for i in range(len(row_pairs)):
                    for j in range(i + 1, len(row_pairs)):
                        if row_pairs[j][0] < row_pairs[i][0]:
                            temp_pair = row_pairs[i]
                            row_pairs[i] = row_pairs[j]
                            row_pairs[j] = temp_pair
                key = tuple(row_pairs)

                if key not in seen:
                    seen.add(key)
                    unique_docs.append(row)

            return self._convert_object_ids(unique_docs)

        # 7. Projection / Aggregates
        final = self._apply_projection(stmt.fields, docs, plan)
        final = self._convert_object_ids(final)
        return final

    # ------------------------------------------------------------
    # MATERIALIZE SUBQUERY
    # ------------------------------------------------------------
    def _materialize_subquery(self, sub: SelectStmt, plan: List[str]) -> str:
        """
        Evaluate a subquery and store results in a temp collection.
        Uses temp DB if DB-level write is available, otherwise temp collection in active DB.
        """

        _log_debug("Evaluating subquery bottom-up")

        rows = self._eval_select(sub, plan)

        # Determine temp collection name
        temp_name = f"__tmp_{uuid.uuid4().hex}__"

        # If DB-level write is available, create a temp DB
        if self.has_db_write:
            if not self._temp_db_name:
                self._temp_db_name = f"__tempdb_{uuid.uuid4().hex}__"
                plan.append(f"CREATE TEMP DB: {self._temp_db_name}")
                _log_debug(f"Created temp DB: {self._temp_db_name}")

            temp_db = self.mongo.client[self._temp_db_name]
            coll = temp_db[temp_name]
        else:
            # Use active DB
            coll = self.mongo.mm_database[temp_name]

        plan.append(f"CREATE TEMP COLLECTION: {temp_name}")
        _log_debug(f"Inserting {len(rows)} rows into temp collection {temp_name}")

        if rows:
            coll.insert_many(rows)

        return temp_name

    # ------------------------------------------------------------
    # WHERE COMPILATION
    # ------------------------------------------------------------
    def _compile_where(self, expr: Any, plan: List[str]) -> Dict[str, Any]:
        if isinstance(expr, BinaryExpr):
            left = self._compile_where(expr.left, plan)
            right = self._compile_where(expr.right, plan)
            if expr.op == "AND":
                return {"$and": [left, right]}
            if expr.op == "OR":
                return {"$or": [left, right]}
            raise Exception(f"Unknown boolean operator {expr.op}")

        if isinstance(expr, CompareExpr):
            field = expr.field
            op = expr.op
            val = expr.value
            if op == "=":
                return {field: val}
            if op == "<":
                return {field: {"$lt": val}}
            if op == "<=":
                return {field: {"$lte": val}}
            if op == ">":
                return {field: {"$gt": val}}
            if op == ">=":
                return {field: {"$gte": val}}
            raise Exception(f"Unknown comparison operator {op}")

        if isinstance(expr, LikeExpr):
            regex = expr.pattern.replace("%", ".*")
            return {expr.field: {"$regex": f"^{regex}$"}}

        if isinstance(expr, InSubqueryExpr):
            plan.append("WHERE IN (subquery) → materializing")
            temp_name = self._materialize_subquery(expr.subquery, plan)
            temp_coll = self.mongo.mm_database[temp_name]
            vals = []
            for d in temp_coll.find():
                vals.append(d.get(expr.subquery.fields[0]))
            return {expr.field: {"$in": vals}}

        raise Exception("Unsupported WHERE expression type")

    # ------------------------------------------------------------
    # PROJECTION & AGGREGATES
    # ------------------------------------------------------------
    def _apply_projection(self, fields: List[Any], docs: List[Dict[str, Any]], plan: List[str]):
        # Aggregates
        if len(fields) == 1 and isinstance(fields[0], str):
            f = fields[0]
            has_paren = False
            index = 0
            while index < len(f):
                if f[index] == "(":
                    has_paren = True
                    break
                index += 1
            ends_with_paren = False
            if len(f) > 0 and f[len(f) - 1] == ")":
                ends_with_paren = True
            if has_paren and ends_with_paren:
                func, arg = f.split("(", 1)
                func = func.upper()
                arg = arg[:-1]

                if func == "COUNT":
                    plan.append("APPLY AGGREGATE: COUNT")
                    if arg == "*":
                        return [{"COUNT": len(docs)}]
                    count_value = 0
                    for d in docs:
                        if arg in d:
                            count_value += 1
                    return [{"COUNT": count_value}]

                if func == "SUM":
                    plan.append("APPLY AGGREGATE: SUM")
                    total_sum = 0
                    for d in docs:
                        total_sum += d.get(arg, 0)
                    return [{"SUM": total_sum}]

                if func == "AVG":
                    plan.append("APPLY AGGREGATE: AVG")
                    vals = []
                    for d in docs:
                        vals.append(d.get(arg, 0))
                    if vals:
                        total = 0
                        for v in vals:
                            total += v
                        avg_value = total / len(vals)
                    else:
                        avg_value = 0
                    return [{"AVG": avg_value}]

                if func == "MIN":
                    plan.append("APPLY AGGREGATE: MIN")
                    vals = []
                    for d in docs:
                        if arg in d:
                            vals.append(d.get(arg))
                    if vals:
                        current_min = vals[0]
                        idx = 1
                        while idx < len(vals):
                            if vals[idx] < current_min:
                                current_min = vals[idx]
                            idx += 1
                        min_value = current_min
                    else:
                        min_value = None
                    return [{"MIN": min_value}]

                if func == "MAX":
                    plan.append("APPLY AGGREGATE: MAX")
                    vals = []
                    for d in docs:
                        if arg in d:
                            vals.append(d.get(arg))
                    if vals:
                        current_max = vals[0]
                        idx = 1
                        while idx < len(vals):
                            if vals[idx] > current_max:
                                current_max = vals[idx]
                            idx += 1
                        max_value = current_max
                    else:
                        max_value = None
                    return [{"MAX": max_value}]

        # Normal projection
        out = []
        for d in docs:
            row = {}
            for f in fields:
                if f == "*":
                    row.update(d)
                else:
                    row[f] = d.get(f)
            out.append(row)
        return out

    # ------------------------------------------------------------
    # SQL RECONSTRUCTION (for debugging)
    # ------------------------------------------------------------
    def _reconstruct_sql(self, stmt: SelectStmt) -> str:
        return "<SQL reconstruction omitted>"

    # ------------------------------------------------------------
    # INSERT EXECUTION
    # ------------------------------------------------------------
    def _execute_insert(self, stmt: InsertStmt) -> Dict[str, Any]:
        if self.read_only:
            raise Exception("User does not have write privileges (INSERT blocked).")

        coll = self.mongo.mm_database[stmt.table]

        if stmt.columns:
            doc = {}
            index = 0
            while index < len(stmt.columns) and index < len(stmt.values):
                col = stmt.columns[index]
                val = stmt.values[index]
                doc[col] = val
                index += 1
        else:
            # No column list → assume full row
            doc = {}
            i = 0
            while i < len(stmt.values):
                key = f"col{i+1}"
                doc[key] = stmt.values[i]
                i += 1

        _log_light(f"INSERT INTO {stmt.table}")
        coll.insert_one(doc)

        return {
            "sql": f"INSERT INTO {stmt.table}",
            "mongo_plan": "insert_one()",
            "result": {"inserted": 1}
        }

    # ------------------------------------------------------------
    # UPDATE EXECUTION
    # ------------------------------------------------------------
    def _execute_update(self, stmt: UpdateStmt) -> Dict[str, Any]:
        if self.read_only:
            raise Exception("User does not have write privileges (UPDATE blocked).")

        coll = self.mongo.mm_database[stmt.table]

        # WHERE
        mongo_filter = {}
        if stmt.where:
            mongo_filter = self._compile_where(stmt.where, [])

        # SET
        set_fields = {}
        for item in stmt.set_items:
            set_fields[item.column] = item.value
        update_doc = {"$set": set_fields}

        _log_light(f"UPDATE {stmt.table}")
        res = coll.update_many(mongo_filter, update_doc)

        return {
            "sql": f"UPDATE {stmt.table}",
            "mongo_plan": f"update_many(filter={mongo_filter}, update={update_doc})",
            "result": {"matched": res.matched_count, "modified": res.modified_count}
        }

    # ------------------------------------------------------------
    # DELETE EXECUTION
    # ------------------------------------------------------------
    def _execute_delete(self, stmt: DeleteStmt) -> Dict[str, Any]:
        if self.read_only:
            raise Exception("User does not have write privileges (DELETE blocked).")

        coll = self.mongo.mm_database[stmt.table]

        mongo_filter = {}
        if stmt.where:
            mongo_filter = self._compile_where(stmt.where, [])

        _log_light(f"DELETE FROM {stmt.table}")
        res = coll.delete_many(mongo_filter)

        return {
            "sql": f"DELETE FROM {stmt.table}",
            "mongo_plan": f"delete_many(filter={mongo_filter})",
            "result": {"deleted": res.deleted_count}
        }
    
    def _execute_show_tables(self):
        if self.mongo.mm_database is None:
            return {
                "sql": "SHOW TABLES",
                "mongo_plan": "SHOW TABLES",
                "result": "No active database selected."
            }

        tables = self.mongo.mm_database.list_collection_names()

        return {
            "sql": "SHOW TABLES",
            "mongo_plan": "LIST COLLECTIONS",
            "result": tables
        }

    def _execute_show_databases(self):
        try:
            dbs = self.mongo.mm_client.list_database_names()
            return {
                "sql": "SHOW DATABASES",
                "mongo_plan": "LIST DATABASES",
                "result": dbs
            }
        except Exception as e:
            return {
                "sql": "SHOW DATABASES",
                "mongo_plan": "LIST DATABASES",
                "result": f"Error listing databases: {str(e)}"
            }
        
    def _execute_show_indexes(self, stmt: ShowIndexesStmt):
        table = stmt.table

        if self.mongo.mm_database is None:
            return {
                "sql": f"SHOW INDEXES FROM {table}",
                "mongo_plan": "SHOW INDEXES",
                "result": "No active database selected."
            }

        collections = self.mongo.mm_database.list_collection_names()
        if table not in collections:
            return {
                "sql": f"SHOW INDEXES FROM {table}",
                "mongo_plan": "SHOW INDEXES",
                "result": f"Table '{table}' does not exist."
            }

        try:
            idx = list(self.mongo.mm_database[table].list_indexes())
            # Convert ObjectId inside index metadata
            cleaned = []
            for d in idx:
                new_doc = {}
                for k, v in d.items():
                    if isinstance(v, ObjectId):
                        new_doc[k] = str(v)
                    else:
                        new_doc[k] = v
                cleaned.append(new_doc)

            return {
                "sql": f"SHOW INDEXES FROM {table}",
                "mongo_plan": f"LIST INDEXES ON {table}",
                "result": cleaned
            }
        except Exception as e:
            return {
                "sql": f"SHOW INDEXES FROM {table}",
                "mongo_plan": "SHOW INDEXES",
                "result": f"Error: {str(e)}"
            }

    def _execute_describe(self, stmt: DescribeStmt) -> Dict[str, Any]:
        if stmt.table not in self.mongo.mm_database.list_collection_names():
            return(f"Table '{stmt.table}' does not exist.")
        coll = self.mongo.mm_database[stmt.table]
        sample_doc = coll.find_one()
        if sample_doc is None:
            return {"sql": f"DESCRIBE {stmt.table}", "mongo_plan": "find_one()", "result": "Collection is empty"}
        else:
            fields = list(sample_doc.keys())
            return {"sql": f"DESCRIBE {stmt.table}", "mongo_plan": "find_one()", "result": {"fields": fields}}
        
    def _execute_help(self, stmt: HelpStmt) -> Dict[str, Any]:
        general_help = "Supported SQL commands: SELECT, INSERT, UPDATE, DELETE, DESCRIBE, HELP. Use HELP <COMMAND> for details."
        if stmt.topic is None:
            return {"sql": "HELP", "mongo_plan": "N/A", "result": general_help}
        
        topic = stmt.topic.upper()
        if topic == "SELECT":
            detail = "SELECT syntax: SELECT [DISTINCT] fields FROM source [WHERE condition] [ORDER BY fields] [LIMIT n] [OFFSET n]"
        elif topic == "INSERT":
            detail = "INSERT syntax: INSERT INTO table [(columns)] VALUES (values)"
        elif topic == "UPDATE":
            detail = "UPDATE syntax: UPDATE table SET column=value [, column=value ...] [WHERE condition]"
        elif topic == "DELETE":
            detail = "DELETE syntax: DELETE FROM table [WHERE condition]"
        elif topic == "DESCRIBE":
            detail = "DESCRIBE syntax: DESCRIBE table"
        elif topic == "SHOW":
            detail = "SHOW syntax: SHOW TABLES | SHOW DATABASES | SHOW INDEXES FROM table"
        elif topic == "TABLES":
            detail = "SHOW TABLES syntax: SHOW TABLES"
        elif topic == "DATABASES":
            detail = "SHOW DATABASES syntax: SHOW DATABASES"
        elif topic == "INDEXES":
            detail = "SHOW INDEXES syntax: SHOW INDEXES FROM table"
        elif topic == "DROP":
            detail = "DROP syntax: DROP TABLE table"
        elif topic == "HELP":
            detail = "HELP syntax: HELP [TOPIC]. If TOPIC is provided, shows details for that command. Otherwise, shows general help."
        else:
            detail = f"No help available for topic '{stmt.topic}'. {general_help}"
        
        return {"sql": f"HELP {stmt.topic}", "mongo_plan": "N/A", "result": detail}
    
    def _execute_drop_table(self, stmt: DropTableStmt) -> Dict[str, Any]:
        table = stmt.table

        # Ensure DB is selected
        if self.mongo.mm_database is None:
            return {
                "sql": f"DROP TABLE {table}",
                "mongo_plan": "DROP TABLE",
                "result": "No active database selected."
            }

        # Check if collection exists
        collections = self.mongo.mm_database.list_collection_names()    
        if table not in collections:
            return {
                "sql": f"DROP TABLE {table}",
                "mongo_plan": "DROP TABLE",
                "result": f"Table '{table}' does not exist."
            }

        # Drop the collection
        try:
            self.mongo.mm_database.drop_collection(table)
            return {
                "sql": f"DROP TABLE {table}",
                "mongo_plan": f"DROP COLLECTION {table}",
                "result": f"Table '{table}' dropped."
            }
        except Exception as e:
            return {
                "sql": f"DROP TABLE {table}",
                "mongo_plan": "DROP TABLE",
                "result": f"Error dropping table: {str(e)}"
            }

    def _convert_object_ids(self, docs):
        cleaned = []
        for d in docs:
            new_doc = {}
            for k, v in d.items():
                if isinstance(v, ObjectId):
                    new_doc[k] = str(v)
                else:
                    new_doc[k] = v
            cleaned.append(new_doc)
        return cleaned
# ============================================================
# END OF SQLToMongoTranslator
# ============================================================

# The module ends here.  No additional helper classes or functions
# are defined beyond this point.  All SQL parsing, privilege
# detection, nested SELECT evaluation, temp DB/collection handling,
# and CRUD execution logic is contained within the classes above.