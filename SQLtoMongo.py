# ---
#  SQLtoMongo.py
# ---
# SQLToMongoTranslator
#
# Executes SQL-style statements against MongoDB via MongoManager.
# Supports:
#   - SELECT with WHERE, ORDER BY, LIMIT, OFFSET
#   - Nested SELECT in FROM (derived tables)
#   - COUNT/SUM/AVG/MIN/MAX, WHERE, ORDER BY, LIMIT, OFFSET.
#   - INSERT, UPDATE, DELETE
#   - Arithmetic operations
#   - HELP command to outline available options
#
# Execution model:
#   - Simple SELECTs run against the current active database (mongo.mm_database).
#   - Nested SELECTs / derived tables use a temporary MongoDB database:
#       _sql_tmp_YYYYMMDD_HHMMSS_<uuid>
#   - Evaluates nested SELECTs from the inside out.
#   - Uses temp DB when DB-level write privileges exist; otherwise temp collections
#     in the active DB (if write allowed there).
#         tmp_select_<uuid>
#   - Original DB is preserved and restored after execution.
#   - Command line is parsed to deal with keywords, parentheses, expressions, and syntax expectations
#   - Parser makes use of many customized Datatypes for logical type assignments for various operations
#   - Execution will continue even after faults, with error output available on default console device to aid in analysis
#   - Materializes subquery results into temp collections (in temp DB or active DB).
#   - Logging controlled by environment variable SQL2MONGO_LOGLEVEL:
#         NONE  = no logging
#         LIGHT = high-level logging
#         DEBUG = deep internal logging
#
# Read-only behavior:
#   - Read-only users can run simple SELECTs and aggregations.
#   - Operations requiring writes (INSERT/UPDATE/DELETE, nested SELECTs with temp DBs)
#     are degraded gracefully with warnings and empty results.
# ---
# # Version History:
# +++
#     v0.1 - May 31, 2026 - This is a wholly new submission that was made to satisfy CS499's Algorithms and Data Structures milestone.
#                               It translates basic (and even some advanced) SQL to Mongo functions, and executes the commands to provide
#                               a method for SQL-savvy users to perform Mongo related operations without having to stumble around on the
#                               Swagger UI.  Code submitted has a lot of features missing, and is still very much a work in progress.
# +++
#     v1.0 - June 21, 2026 - Nearing final updates prior to finishing out the Capstone project for SNHU course CS-499.
#                               Added deal with common SQL functions, deal with arithmetic expressions in command lines, deal
#                               with aliases using AS.  Last enhancement worked on provides JOIN functionality, though this is 
#                               still in need of testing and adjusting (this one was a major ordeal to implement).  The product,
#                               like many existing commercial products, may have issues but can be addressed as they are discovered.
# ---
# Mark Woodford
# SNHU CS499 Computer Science Capstone
# June 21, 2026
# ---

# ---
# Python modules necessary for functionality.  The class needs to be able to operate separately from the python artifact
#     module that's using it.
# ---
from ast import stmt
import os, sys
import re
import uuid
from datetime import datetime
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, List, Optional, Dict
from bson import ObjectId


# ---
# LOGGING HELPERS
# ---

LOGLEVEL = os.getenv("SQL2MONGO_LOGLEVEL", "NONE").upper()

def _log_light(msg: str):
    if LOGLEVEL in ("LIGHT", "DEBUG"):
        print(f"[SQL2MONGO] {msg}")

def _log_debug(msg: str):
    if LOGLEVEL == "DEBUG":
        print(f"[SQL2MONGO DEBUG] {msg}")


# ---
# TOKENIZER
# ---

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
    PLUS = auto()
    MINUS = auto()
    SLASH = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    position: int


# ---
# AST NODES
# ---

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
class ColumnRef:
    def __init__(self, table=None, name=None):
        self.table = table
        self.name = name

    def __repr__(self):
        if self.table:
            return f"{self.table}.{self.name}"
        return self.name

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

@dataclass
class CreateIndexStatement:
    name: str
    table: str
    fields: list   # list of (field_name, direction)
    unique: bool

@dataclass
class DropIndexStatement:
    name: str
    table: str

@dataclass
class ColumnAlias:
    expr: Any
    alias: str

@dataclass
class TableAlias:
    source: Any
    alias: str

@dataclass
class ArithExpr:
    op: str      # '+', '-', '*', '/'
    left: Any
    right: Any

@dataclass
class UnaryExpr:
    op: str      # '-', 'NOT'
    operand: Any

@dataclass
class FuncExpr:
    name: str
    args: List[Any]

@dataclass
class JoinRef:
    left: Any
    right: Any
    on: Any
    join_type: str  # "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS"

@dataclass
class InListExpr:
    field: Any
    list_field: Any  

# ---
# LEXER
# ---

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
                # consume the first character
                ident += self._advance()
                # then consume the rest
                while self._peek().isalnum() or self._peek() == "_":
                    ident += self._advance()

                upper = ident.upper()
                if upper in [
                    "SELECT", "FROM", "WHERE", "ORDER", "BY", "LIMIT", "OFFSET",
                    "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
                    "DISTINCT", "AND", "OR", "LIKE", "IN", "ASC", "DESC",
                    "DESCRIBE", "HELP", "DROP", "TABLE", "INDEX",
                    "SHOW", "TABLES", "DATABASES", "INDEXES", "FROM",
                    "CREATE", "UNIQUE", "ON", "AS", "JOIN", "ON",
                    "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER"
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
            if ch == ".":
                self._advance()
                toks.append(Token(TokenType.DOT, ".", start))
                continue
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
            if ch == "+":
                self._advance()
                toks.append(Token(TokenType.PLUS, "+", start))
                continue
            if ch == "-":
                self._advance()
                toks.append(Token(TokenType.MINUS, "-", start))
                continue
            if ch == "/":
                self._advance()
                toks.append(Token(TokenType.SLASH, "/", start))
                continue

            # Unknown char
            self._advance()

        toks.append(Token(TokenType.EOF, "", self.pos))
        return toks

# ---
# PARSER
# ---

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.index = 0

    def parse_statement(self) -> Any:
        tok = self._current()
        if tok.type == TokenType.KEYWORD:
            if tok.value == "SELECT":
                return self.parse_select()
            if tok.value == "INSERT":
                return self.parse_insert()
            if tok.value == "UPDATE":
                return self.parse_update()
            if tok.value == "DELETE":
                return self.parse_delete()
            if tok.value == "CREATE":
                return self.parse_create_index()
            if tok.value == "DROP":
                return self.parse_drop()
            if tok.value == "SHOW":
                return self.parse_show()
            if tok.value == "DESCRIBE":
                return self.parse_describe()
            if tok.value == "HELP":
                return self.parse_help()

        raise Exception(f"Unsupported SQL command: {tok.value}")

    # ---
    # SELECT
    # ---
    def parse_select(self) -> SelectStmt:
        self._expect_kw("SELECT")
        distinct = False
        if self._current().type == TokenType.KEYWORD and self._current().value == "DISTINCT":
            distinct = True
            self._advance()

        fields = self.parse_select_list()

        self._expect_kw("FROM")
        from_source = self.parse_from_source()

        where = None
        order_by = None
        limit = None
        offset = None

        while self._current().type == TokenType.KEYWORD:
            kw = self._current().value
            if kw == "WHERE":
                self._advance()
                where = self.parse_expr()
            elif kw == "ORDER":
                self._advance()
                self._expect_kw("BY")
                order_by = self.parse_order_by()
            elif kw == "LIMIT":
                self._advance()
                if self._current().type == TokenType.NUMBER:
                    limit = int(self._advance().value)
            elif kw == "OFFSET":
                self._advance()
                if self._current().type == TokenType.NUMBER:
                    offset = int(self._advance().value)
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
        while self._current().type == TokenType.COMMA:
            self._advance()
            items.append(self.parse_select_item())
        return items

    def parse_select_item(self) -> Any:
        tok = self._current()

        # Handle "*"
        if tok.type == TokenType.STAR:
            self._advance()
            return "*"

        # Handle subquery in SELECT list:  (SELECT ...)
        if tok.type == TokenType.LPAREN:
            self._advance()
            expr = self.parse_add()
            self._expect(TokenType.RPAREN)
            return self._maybe_parse_column_alias(expr)

        # Handle full SQL expressions (identifiers, arithmetic, functions, parentheses, etc.)
        expr = self.parse_add()

        # Explicit alias: AS alias
        if self._current().type == TokenType.KEYWORD and self._current().value.upper() == "AS":
            self._advance()
            alias = self._expect(TokenType.IDENT).value
            return ColumnAlias(expr=expr, alias=alias)

        # Implicit alias: expr alias
        if self._current().type == TokenType.IDENT:
            alias = self._advance().value
            return ColumnAlias(expr=expr, alias=alias)

        # No alias
        return expr
    
    def parse_from_source(self) -> Any:
        # Parse the initial table or subquery
        source = self._parse_single_from_source()

        # Parse zero or more JOINs
        while self._current().type == TokenType.KEYWORD and self._current().value in ("JOIN", "INNER", "LEFT", "RIGHT"):
            join_type = "INNER"

            kw = self._current().value
            if kw == "JOIN":
                self._advance()
            elif kw == "INNER":
                self._advance()
                self._expect_kw("JOIN")
                join_type = "INNER"
            elif kw == "LEFT":
                self._advance()
                if self._current().type == TokenType.KEYWORD and self._current().value == "OUTER":
                    self._advance()
                self._expect_kw("JOIN")
                join_type = "LEFT"
            elif kw == "RIGHT":
                self._advance()
                if self._current().type == TokenType.KEYWORD and self._current().value == "OUTER":
                    self._advance()
                self._expect_kw("JOIN")
                join_type = "RIGHT"

            # Right side table/subquery
            right = self._parse_single_from_source()

            # ON clause
            self._expect_kw("ON")
            on_expr = self.parse_expr()

            # RIGHT JOIN → rewrite as LEFT JOIN
            if join_type == "RIGHT":
                source = JoinRef(
                    left=right,
                    right=source,
                    on=self._flip_join_condition(on_expr),
                    join_type="LEFT"
                )
            else:
                source = JoinRef(
                    left=source,
                    right=right,
                    on=on_expr,
                    join_type=join_type
                )

        return source


    def _parse_single_from_source(self) -> Any:
        tok = self._current()

        # Subquery source
        if tok.type == TokenType.LPAREN:
            self._advance()
            sub_select = self.parse_select()
            self._expect(TokenType.RPAREN)
            source = SubqueryRef(select=sub_select)
            return self._maybe_parse_table_alias(source)

        # Table reference
        if tok.type == TokenType.IDENT:
            table = TableRef(name=self._advance().value)
            return self._maybe_parse_table_alias(table)

        raise Exception(f"Invalid FROM source: {tok.value}")

    def parse_order_by(self) -> List[tuple]:
        items: List[tuple] = []
        field = self._expect(TokenType.IDENT).value
        direction = 1
        if self._current().type == TokenType.KEYWORD and self._current().value in ("ASC", "DESC"):
            if self._current().value == "DESC":
                direction = -1
            self._advance()
        items.append((field, direction))
        while self._current().type == TokenType.COMMA:
            self._advance()
            field = self._expect(TokenType.IDENT).value
            direction = 1
            if self._current().type == TokenType.KEYWORD and self._current().value in ("ASC", "DESC"):
                if self._current().value == "DESC":
                    direction = -1
                self._advance()
            items.append((field, direction))
        return items

    # ---
    # WHERE expressions
    # ---
    def parse_expr(self) -> Any:
        return self.parse_or()

    def parse_or(self) -> Any:
        left = self.parse_and()
        while self._current().type == TokenType.KEYWORD and self._current().value == "OR":
            op = self._advance().value
            right = self.parse_and()
            left = BinaryExpr(op=op, left=left, right=right)
        return left

    def parse_and(self) -> Any:
        left = self.parse_primary_cond()
        while self._current().type == TokenType.KEYWORD and self._current().value == "AND":
            op = self._advance().value
            right = self.parse_primary_cond()
            left = BinaryExpr(op=op, left=left, right=right)
        return left

    def parse_primary_cond(self) -> Any:
        tok = self._current()

        # Parenthesized condition
        if tok.type == TokenType.LPAREN:
            self._advance()
            inner = self.parse_expr()
            self._expect(TokenType.RPAREN)
            return inner

        # First parse the left-hand expression (ColumnRef, literal, function, etc.)
        left = self.parse_add()

        # Now check for IN
        if self._current().type == TokenType.KEYWORD and self._current().value == "IN":
            self._advance()  # consume IN

            # IN (subquery)
            if self._current().type == TokenType.LPAREN:
                self._advance()
                sub = self.parse_select()
                self._expect(TokenType.RPAREN)
                return InSubqueryExpr(field=left, subquery=sub)

            # IN column/list field
            right = self.parse_add()
            return InListExpr(field=left, list_field=right)

        # Comparison operator
        tok = self._current()
        if tok.type in (TokenType.EQ, TokenType.LT, TokenType.LTE, TokenType.GT, TokenType.GTE):
            op = self._advance().value
            right = self.parse_add()
            return CompareExpr(field=left, op=op, value=right)

        raise Exception(f"Unsupported condition starting at {tok.value}")

    def parse_value(self) -> Any:
        tok = self._current()
        if tok.type == TokenType.STRING:
            return self._advance().value
        if tok.type == TokenType.NUMBER:
            return int(self._advance().value)
        if tok.type == TokenType.IDENT:
            return self._advance().value
        raise Exception(f"Unsupported value token: {tok.value}")
    
    def parse_add(self) -> Any:
        node = self.parse_mul()

        while self._current().type in (TokenType.PLUS, TokenType.MINUS):
            op = self._advance().value
            right = self.parse_mul()
            node = ArithExpr(op=op, left=node, right=right)

        return node
    
    def parse_mul(self) -> Any:
        node = self.parse_unary()

        while self._current().type in (TokenType.STAR, TokenType.SLASH):
            op = self._advance().value
            right = self.parse_unary()
            node = ArithExpr(op=op, left=node, right=right)

        return node
    
    def parse_unary(self) -> Any:
        tok = self._current()

        # Unary NOT
        if tok.type == TokenType.KEYWORD and tok.value == "NOT":
            self._advance()
            operand = self.parse_unary()
            return UnaryExpr(op="NOT", operand=operand)

        # Unary minus
        if tok.type == TokenType.MINUS:
            self._advance()
            operand = self.parse_unary()
            return UnaryExpr(op="-", operand=operand)

        return self.parse_primary()
    
    def parse_primary(self) -> Any:
        tok = self._current()

        # Parenthesized expression
        if tok.type == TokenType.LPAREN:
            self._advance()
            expr = self.parse_add()
            self._expect(TokenType.RPAREN)
            return expr

        # String literal
        if tok.type == TokenType.STRING:
            return self._advance().value

        # Number literal
        if tok.type == TokenType.NUMBER:
            return int(self._advance().value)

        # Identifier or function call
        if tok.type == TokenType.IDENT:
            ident = self._advance().value

            # Function call?
            if self._current().type == TokenType.LPAREN:
                self._advance()
                args = []

                if self._current().type != TokenType.RPAREN:
                    args.append(self.parse_add())
                    while self._current().type == TokenType.COMMA:
                        self._advance()
                        args.append(self.parse_add())

                self._expect(TokenType.RPAREN)
                return FuncExpr(name=ident, args=args)

            # Dotted identifier: table.column
            if self._current().type == TokenType.DOT:
                self._advance()  # consume '.'
                field = self._expect(TokenType.IDENT).value
                return ColumnRef(table=ident, name=field)

            # Simple identifier
            return ColumnRef(name=ident)

        raise Exception(f"Unexpected token in expression: {tok.value}")

    # ---
    # INSERT
    # ---
    def parse_insert(self) -> InsertStmt:
        self._expect_kw("INSERT")
        self._expect_kw("INTO")
        table = self._expect(TokenType.IDENT).value

        columns: Optional[List[str]] = None
        if self._current().type == TokenType.LPAREN:
            self._advance()
            columns = []
            columns.append(self._expect(TokenType.IDENT).value)
            while self._current().type == TokenType.COMMA:
                self._advance()
                columns.append(self._expect(TokenType.IDENT).value)
            self._expect(TokenType.RPAREN)

        self._expect_kw("VALUES")
        self._expect(TokenType.LPAREN)
        values: List[Any] = []
        values.append(self.parse_value())
        while self._current().type == TokenType.COMMA:
            self._advance()
            values.append(self.parse_value())
        self._expect(TokenType.RPAREN)

        return InsertStmt(table=table, columns=columns, values=values)

    # ---
    # UPDATE
    # ---
    def parse_update(self) -> UpdateStmt:
        self._expect_kw("UPDATE")
        table = self._expect(TokenType.IDENT).value
        self._expect_kw("SET")
        set_items: List[UpdateSetItem] = []
        set_items.append(self.parse_set_item())
        while self._current().type == TokenType.COMMA:
            self._advance()
            set_items.append(self.parse_set_item())

        where = None
        if self._current().type == TokenType.KEYWORD and self._current().value == "WHERE":
            self._advance()
            where = self.parse_expr()

        return UpdateStmt(table=table, set_items=set_items, where=where)

    def parse_set_item(self) -> UpdateSetItem:
        col = self._expect(TokenType.IDENT).value
        self._expect(TokenType.EQ)
        val = self.parse_add()
        return UpdateSetItem(column=col, value=val)

    # ---
    # DELETE
    # ---
    def parse_delete(self) -> DeleteStmt:
        self._expect_kw("DELETE")
        self._expect_kw("FROM")
        table = self._expect(TokenType.IDENT).value
        where = None
        if self._current().type == TokenType.KEYWORD and self._current().value == "WHERE":
            self._advance()
            where = self.parse_expr()
        return DeleteStmt(table=table, where=where)
    
    # ---
    # CREATE
    # ---
    def parse_create_index(self):
        self._expect_kw("CREATE")

        unique = False
        if self._current().value == "UNIQUE":
            unique = True
            self._advance()

        self._expect_kw("INDEX")
        name = self._expect(TokenType.IDENT).value

        self._expect_kw("ON")
        table = self._expect(TokenType.IDENT).value

        self._expect(TokenType.LPAREN)

        fields = []
        while True:
            field = self._expect(TokenType.IDENT).value

            direction = "asc"
            if self._current().type == TokenType.KEYWORD and self._current().value in ("ASC", "DESC"):
                direction = self._advance().value.lower()

            fields.append((field, direction))

            if self._current().type == TokenType.COMMA:
                self._advance()
                continue
            break

        self._expect(TokenType.RPAREN)

        return CreateIndexStatement(name=name, table=table, fields=fields, unique=unique)
    
    # ---
    # DROP (INDEX or TABLE)
    # ---
    def parse_drop(self):
        self._expect_kw("DROP")
        if self._peek_next_keyword("TABLE"):
            self._expect_kw("TABLE")
            table = self._expect(TokenType.IDENT).value
            return DropTableStmt(table=table)

        if self._peek_next_keyword("INDEX"):
            self._expect_kw("INDEX")
            name = self._expect(TokenType.IDENT).value
            self._expect_kw("ON")
            table = self._expect(TokenType.IDENT).value
            return DropIndexStatement(name=name, table=table)
        
        if self._peek_next_keyword("DATABASE"):
            return "Function not yet implemented, DROP DATABASE"
        if self._peek_next_keyword("USER"):
            return "Function not yet implemented, DROP USER"
        if self._peek_next_keyword("VIEW"):
            return "Function not yet implemented, DROP VIEW"

        raise Exception("Unsupported DROP command")
    
    # ---
    # SHOW
    # ---
    def parse_show(self):
        self._expect_kw("SHOW")

        # SHOW TABLES
        if self._current().type == TokenType.KEYWORD and self._current().value == "TABLES":
            self._advance()
            return ShowTablesStmt()

        # SHOW DATABASES
        if self._current().type == TokenType.KEYWORD and self._current().value == "DATABASES":
            self._advance()
            return ShowDatabasesStmt()

        # SHOW INDEXES FROM <table>
        if self._current().type == TokenType.KEYWORD and self._current().value == "INDEXES":
            self._advance()
            self._expect_kw("FROM")
            table = self._expect(TokenType.IDENT).value
            return ShowIndexesStmt(table=table)

        return "Unsupported SHOW command."
    
    # ---
    # DESCRIBE
    # ---
    def parse_describe(self) -> str:
        self._expect_kw("DESCRIBE")
        table = self._expect(TokenType.IDENT).value
        return DescribeStmt(table=table)
    
    # ---
    # HELP
    # ---
    def parse_help(self) -> HelpStmt:
        # Consume the HELP keyword
        self._expect_kw("HELP")

        # Optional topic: HELP SELECT, HELP INSERT, etc.
        topic = None
        if self._current().type in (TokenType.IDENT, TokenType.KEYWORD) and self._current().type != TokenType.EOF:
            topic = self._advance().value

        return HelpStmt(topic=topic)
    # ---
    # HELPER FUNCTIONS
    # ---
    def _peek_keyword(self, value: str) -> bool:
            tok = self._current()
            return tok.type == TokenType.KEYWORD and tok.value.upper() == value.upper()    
    
    def _peek(self):
        if self.index + 1 < len(self.tokens):
            return self.tokens[self.index + 1]
        return Token(TokenType.EOF, "", None)
    
    def _peek_next_keyword(self, value: str) -> bool:
        tok = self._peek()  # look ahead one token
        return tok.value

    def _value_to_sql(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        return str(value)

    def _current(self) -> Token:
        return self.tokens[self.index]

    def _advance(self) -> Token:
        tok = self._current()
        self.index += 1
        return tok

    def _expect_kw(self, kw: str) -> Token:
        tok = self._current()
        if tok.type != TokenType.KEYWORD or tok.value.upper() != kw:
            raise Exception(f"Expected keyword {kw}, got {tok.value}")
        return self._advance()

    def _expect(self, t: TokenType) -> Token:
        tok = self._current()
        if tok.type != t:
            raise Exception(f"Expected {t}, got {tok.type}")
        return self._advance()

    def _maybe_parse_column_alias(self, expr: Any) -> Any:
        # Explicit AS alias
        if self._current().type == TokenType.KEYWORD and self._current().value == "AS":
            self._advance()
            alias = self._expect(TokenType.IDENT).value
            return ColumnAlias(expr=expr, alias=alias)

        # Implicit alias: expr alias
        if self._current().type == TokenType.IDENT:
            alias = self._advance().value
            return ColumnAlias(expr=expr, alias=alias)

        return expr
    
    def _maybe_parse_table_alias(self, source):
        # Explicit alias:  table AS alias
        if self._current().type == TokenType.KEYWORD and self._current().value == "AS":
            self._advance()
            alias = self._expect(TokenType.IDENT).value
            return TableAlias(source=source, alias=alias)

        # Implicit alias:  table alias
        if self._current().type == TokenType.IDENT:
            next_val = self._current().value.upper()

            # Do NOT treat SQL clause keywords as aliases
            if next_val not in (
                "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS",
                "WHERE", "ORDER", "LIMIT", "OFFSET", "GROUP", "HAVING", "ON"
            ):
                alias = self._advance().value
                return TableAlias(source=source, alias=alias)

        return source
    
# ---
# SQL ENGINE
# ---

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
    
    # ---
    # PRIVILEGE DETECTION
    # ---
    def _detect_privileges(self):

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

    # ---
    # SQL ENTRY POINT
    # ---
    def translate_sql(self, sql: str) -> Dict[str, Any]:
        sql = sql.strip()
        if sql.endswith(";"):
            sql = sql[:-1]

        lexer = Lexer(sql)
        tokens = lexer.tokens()
        parser = Parser(tokens)
        stmt = parser.parse_statement()

        try:
            if isinstance(stmt, SelectStmt):
                return self._execute_select(stmt)
            if isinstance(stmt, InsertStmt):
                return self._execute_insert(stmt)
            if isinstance(stmt, UpdateStmt):
                return self._execute_update(stmt)
            if isinstance(stmt, DeleteStmt):
                return self._execute_delete(stmt)
            if isinstance(stmt, CreateIndexStatement):
                return self._execute_create_index(stmt)
            if isinstance(stmt, DropTableStmt):
                return self._execute_drop_table(stmt)
            if isinstance(stmt, DropIndexStatement):
                return self._execute_drop_index(stmt)
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

        finally:
            # ---
            self._cleanup_temp_collections()

    # ---
    # SELECT EXECUTION (TOP LEVEL)
    # ---
    def _execute_select(self, stmt: SelectStmt) -> Dict[str, Any]:
        _log_light("Executing SELECT statement")

        plan_lines: List[str] = []
        result = self._eval_select(stmt, plan_lines)

        return {
            "sql": str(stmt),
            "mongo": {},
            "mongo_plan": {},
            "result": result,
            "plan": plan_lines,
        }

    # ---
    # BOTTOM-UP SELECT EVALUATION
    # ---
    def _eval_from_source(self, source: Any, plan: List[str]):
        if isinstance(source, JoinRef):
            return self._eval_join(source, plan)
        if isinstance(source, TableAlias):
            return self._eval_table_alias(source, plan)
        if isinstance(source, TableRef):
            return self._eval_table(source, plan)
        if isinstance(source, SubqueryRef):
            return self._eval_subquery(source.select, plan)
        raise Exception(f"Unsupported FROM source: {source}")
    
    def _eval_select(self, stmt: SelectStmt, plan: List[str]) -> List[Dict[str, Any]]:
        # If FROM contains a JOIN, pipeline must NOT be used
        if isinstance(stmt.from_source, JoinRef):
            plan.append("JOIN detected → using JOIN engine")
            return self._eval_select_join(stmt, plan)

        # Otherwise use the existing pipeline/find logic
        return self._eval_select_nopipeline(stmt, plan)
    
    def _eval_select_join(self, stmt: SelectStmt, plan: List[str]):
        # Evaluate FROM (JOIN)
        rows = self._eval_from_source(stmt.from_source, plan)

        # WHERE
        if stmt.where:
            rows = [r for r in rows if self._eval_where(stmt.where, r)]

        # ORDER BY
        if stmt.order_by:
            for field, direction in reversed(stmt.order_by):
                rows.sort(key=lambda r: self._eval_order_key(field, r),
                        reverse=(direction == -1))

        # LIMIT / OFFSET
        if stmt.offset:
            rows = rows[stmt.offset:]
        if stmt.limit:
            rows = rows[:stmt.limit]

        # Projection
        return self._apply_projection(stmt.fields, rows, plan)
    
    def _eval_select_nopipeline(self, stmt: SelectStmt, plan: List[str]) -> List[Dict[str, Any]]:

        # Resolve FROM source into rows (tables, subqueries, joins)
        rows = self._eval_from_source(stmt.from_source, plan)

        # Materialize rows into a temp collection so Mongo can query it
        temp_name = self._materialize_temp(rows)
        base_coll = self.mongo.mm_database[temp_name]

        # WHERE
        mongo_filter = {}
        if stmt.where:
            mongo_filter = self._compile_where(stmt.where, plan)
            plan.append(f"WHERE FILTER: {mongo_filter}")

        # ORDER BY
        sort_spec = None
        if stmt.order_by:
            sort_spec = []
            for field, direction in stmt.order_by:
                sort_spec.append((field, direction))
            plan.append(f"ORDER BY: {sort_spec}")

        # LIMIT / OFFSET
        limit = stmt.limit
        offset = stmt.offset
        if limit is not None:
            plan.append(f"LIMIT: {limit}")
        if offset is not None:
            plan.append(f"OFFSET: {offset}")

        # Detect expressions in SELECT list
        needs_pipeline = False
        for f in stmt.fields:
            if isinstance(f, ColumnAlias) and not isinstance(f.expr, str):
                needs_pipeline = True
                break
        if needs_pipeline:
            return self._eval_select_pipeline(stmt, plan)

        # Execute Mongo query
        cursor = base_coll.find(mongo_filter)
        if sort_spec:
            cursor = cursor.sort(sort_spec)
        if offset:
            cursor = cursor.skip(offset)
        if limit:
            cursor = cursor.limit(limit)

        docs = list(cursor)

        # DISTINCT
        if stmt.distinct:
            plan.append("APPLY DISTINCT")
            projected = self._apply_projection(stmt.fields, docs, plan)

            # Deduplicate rows by converting dicts to tuples of sorted items
            seen = set()
            unique = []
            for row in projected:
                key = tuple(sorted(row.items()))
                if key not in seen:
                    seen.add(key)
                    unique.append(row)

            return self._convert_object_ids(unique)

        # Projection / Aggregates
        final = self._apply_projection(stmt.fields, docs, plan)
        final = self._convert_object_ids(final)
        return final

    # ---
    # MATERIALIZE SUBQUERY
    # ---
    def _materialize_subquery(self, sub: SelectStmt, plan: List[str]) -> str:
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

    # ---
    # WHERE COMPILATION
    # ---
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
            left = expr.field
            right = expr.value
            op = expr.op

            left_is_expr = not isinstance(left, str)
            right_is_expr = not isinstance(right, (str, int, float))

            # If either side is an expression → use $expr
            if left_is_expr or right_is_expr:
                return self._compile_where_expr(expr, plan)

            # Otherwise fall back to simple comparison
            return self._compile_simple_compare(expr, plan)

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

    # ---
    # PROJECTION & AGGREGATES
    # ---
    def _apply_projection(self, fields, docs, plan):
        projected = []

        for d in docs:
            row = {}

            for f in fields:
                # Wildcard
                if f == "*":
                    for k, v in d.items():
                        row[k] = v
                    continue

                # ColumnAlias: use alias as key
                if isinstance(f, ColumnAlias):
                    alias = f.alias
                    expr = f.expr

                    compiled = self._compile_expr(expr)
                    row[alias] = self._eval_compiled_expr(compiled, d)

                    continue

                # Plain field name
                if isinstance(f, str):
                    row[f] = d.get(f)
                    continue

            projected.append(row)

        return projected

    # ---
    # SQL RECONSTRUCTION (for debugging)
    # ---
    def _reconstruct_sql(self, stmt: SelectStmt) -> str:
        return "<SQL reconstruction omitted>"

    # ---
    # INSERT EXECUTION
    # ---
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

    # ---
    # UPDATE EXECUTION
    # ---
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

    # ---
    # DELETE EXECUTION
    # ---
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

    def _execute_create_index(self, stmt: CreateIndexStatement):
        # select collection
        self.mongo.set_collection(stmt.table)

        # build request dict in the format MongoManager expects
        req = {
            "fields": [
                {"name": field, "direction": direction}
                for field, direction in stmt.fields
            ],
            "unique": stmt.unique,
            "name": stmt.name,
        }

        result = self.mongo.create_index(req)
        return {
            "sql": f"CREATE INDEX {stmt.name} ON {stmt.table}("
                    + ", ".join(f"{f} {d.upper()}" for f, d in stmt.fields)
                    + ")",
            "mongo_plan": "CREATE a Mongo index using create_index() with the parameters: " + str(req),
            "result": "Created index " + req['name'] + ".",
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
        general_help = "Supported SQL commands: SELECT, CREATE, INSERT, UPDATE, DELETE, DESCRIBE, SHOW, DROP, HELP. Use HELP <COMMAND> for details."
        if stmt.topic is None:
            return {"sql": "HELP", "mongo_plan": "N/A", "result": general_help}
        
        topic = stmt.topic.upper()
        if topic == "SELECT":
            detail = "SELECT syntax: SELECT [DISTINCT] fields FROM source [WHERE condition] [ORDER BY fields] [LIMIT n] [OFFSET n]"
        elif topic == "CREATE":
            detail = "CREATE syntax: CREATE [UNIQUE] INDEX idx ON table(field)"
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
            detail = "DROP syntax: DROP TABLE | DROP INDEX"
        elif topic == "TABLE":
            detail = "DROP TABLE syntax: DROP TABLE tablename"
        elif topic == "INDEX":
            detail = "DROP INDEX syntax: DROP INDEX idx_name ON table"
        elif topic == "HELP":
            detail = "HELP syntax: HELP [TOPIC]. If TOPIC is provided, shows details for that command. Otherwise, shows general help."
        else:
            detail = f"No help available for topic '{stmt.topic}'. {general_help}"
        
        return {"sql": f"HELP {stmt.topic}", "mongo_plan": "N/A", "result": detail}

    def _execute_drop_index(self, stmt: DropIndexStatement):
        self.mongo.mm_database.set_collection(stmt.table)
        try:
            result = self.mongo.drop_index(stmt.name)
            ret_string = "Dropped index " + stmt.name
        except Exception as e:
            ret_status = "Error trying to drop index: " + e
        return {
                "sql": f"DROP INDEX {stmt.name}",
                "mongo_plan": "Execute drop_index() on index" + stmt.name,
                "result": ret_string,
        }
         

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
    
    def _maybe_convert_objectid(self, field, value):
        if field == "_id" and isinstance(value, str):
            if len(value) == 24:
                try:
                    return(ObjectId(value))
                except Exception:
                    return(value)
        return value

    def _eval_select_pipeline(self, stmt, plan):
        pipeline = []
        rows = self._eval_from_source(stmt.from_source, plan)
        temp_name = self._materialize_temp(rows)
        base_coll = self.mongo.mm_database[temp_name]

        # WHERE
        if stmt.where:
            mongo_filter = self._compile_where(stmt.where, plan)
            pipeline.append({"$match": mongo_filter})

        # PROJECT
        proj = {}

        for f in stmt.fields:
            if isinstance(f, ColumnAlias):
                proj[f.alias] = self._compile_expr(f.expr)
            elif isinstance(f, str):
                proj[f] = f"${f}"
            elif f == "*":
                # Expand later if needed
                pass

        # Always remove _id to avoid ObjectId serialization errors
        proj["_id"] = 0

        pipeline.append({"$project": proj})
        _log_debug(f"project stage: {proj}")
        print(f"project stage: {proj}")

        # ORDER BY
        if stmt.order_by:
            sort = {field: direction for field, direction in stmt.order_by}
            pipeline.append({"$sort": sort})

        # LIMIT
        if stmt.limit:
            pipeline.append({"$limit": stmt.limit})
        
        docs = base_coll.aggregate(pipeline)
        docs = list(docs)

        return list(docs)
    
    def _compile_expr(self, expr):
        try:
            _log_debug(f"COMPILE EXPR: {expr} ({type(expr)})")

            # Numeric literal
            if isinstance(expr, (int, float)):
                return expr
            
            # Literal string or simple identifier
            if isinstance(expr, str):
                # In this engine, strings in expressions are treated as
                # field references when used in projection.
                return f"${expr}"

            # Column reference (qualified or unqualified)
            if isinstance(expr, ColumnRef):
                return f"${expr.name}"

            # Arithmetic expression
            if isinstance(expr, ArithExpr):
                left = self._compile_expr(expr.left)
                right = self._compile_expr(expr.right)

                if expr.op == "+":
                    return {"$add": [left, right]}
                if expr.op == "-":
                    return {"$subtract": [left, right]}
                if expr.op == "*":
                    return {"$multiply": [left, right]}
                if expr.op == "/":
                    return {"$divide": [left, right]}

            # Unary expression
            if isinstance(expr, UnaryExpr):
                operand = self._compile_expr(expr.operand)
                if expr.op == "-":
                    return {"$multiply": [-1, operand]}
                if expr.op == "NOT":
                    return {"$not": [operand]}

            # Function call
            if isinstance(expr, FuncExpr):
                name = expr.name.upper()
                args = [self._compile_expr(a) for a in expr.args]

                if name == "ABS":
                    return {"$abs": args[0]}
                if name == "LOWER":
                    return {"$toLower": args[0]}
                if name == "UPPER":
                    return {"$toUpper": args[0]}
                if name == "ROUND":
                    # ROUND(x) or ROUND(x, n)
                    if len(args) == 1:
                        return {"$round": [args[0], 0]}
                    return {"$round": args}
                if name == "FLOOR":
                    return {"$floor": args[0]}
                if name == "CEIL":
                    return {"$ceil": args[0]}
                if name == "SQRT":
                    return {"$sqrt": args[0]}
                if name == "EXP":
                    return {"$exp": args[0]}
                if name == "LOG":
                    # LOG(x) → natural log
                    return {"$ln": args[0]}
                if name == "POWER" or name == "POW":
                    return {"$pow": args}
                if name == "CONCAT":
                    return {"$concat": args}

            raise Exception(f"Unsupported expression type: {expr}")

        except Exception as e:
            _log_debug(f"EXPR ERROR: {e}")
            raise

        return None
    
    def _compile_where_expr(self, expr, plan):
        _log_debug(f"WHERE EXPR: {expr}")
        try:
            left = self._compile_expr(expr.field)
            right = self._compile_expr(expr.value)

            op = expr.op

            if op == "=":
                return {"$expr": {"$eq": [left, right]}}
            if op == "<":
                return {"$expr": {"$lt": [left, right]}}
            if op == "<=":
                return {"$expr": {"$lte": [left, right]}}
            if op == ">":
                return {"$expr": {"$gt": [left, right]}}
            if op == ">=":
                return {"$expr": {"$gte": [left, right]}}

            raise Exception(f"Unsupported comparison operator in expression WHERE: {op}")
        except Exception as e:
            _log_debug(f"WHERE EXPR ERROR: {e}")
            raise
    
    def _compile_simple_compare(self, expr, plan):
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

    def _eval_expr(self, expr, row):
        # Column reference
        if isinstance(expr, ColumnRef):
            return row.get(expr.name)

        # Literal values
        if isinstance(expr, (str, int, float)):
            return expr

        # Arithmetic expressions
        if isinstance(expr, ArithExpr):
            left = self._eval_expr(expr.left, row)
            right = self._eval_expr(expr.right, row)
            if expr.op == "+": return left + right
            if expr.op == "-": return left - right
            if expr.op == "*": return left * right
            if expr.op == "/": return left / right

        # Unary expressions
        if isinstance(expr, UnaryExpr):
            val = self._eval_expr(expr.operand, row)
            if expr.op == "-": return -val
            if expr.op == "NOT": return not val

        # Comparison expressions
        if isinstance(expr, CompareExpr):
            left = self._eval_expr(expr.field, row)
            right = self._eval_expr(expr.value, row)
            if expr.op == "=": return left == right
            if expr.op == "<": return left < right
            if expr.op == "<=": return left <= right
            if expr.op == ">": return left > right
            if expr.op == ">=": return left >= right

        # LIKE
        if isinstance(expr, LikeExpr):
            val = row.get(expr.field) or ""
            pattern = expr.pattern.replace("%", ".*")
            return re.match(pattern, val) is not None

        # IN (subquery)
        if isinstance(expr, InSubqueryExpr):
            sub_rows = self._eval_select(expr.subquery)
            left_val = row.get(expr.field)
            return any(left_val == r.get(expr.field) for r in sub_rows)

        # IN (list field)
        if isinstance(expr, InListExpr):
            left_val = self._eval_expr(expr.field, row)
            right_list = self._eval_expr(expr.list_field, row)
            if isinstance(right_list, list):
                return left_val in right_list
            return False

        # Boolean AND/OR
        if isinstance(expr, BinaryExpr):
            left = self._eval_expr(expr.left, row)
            right = self._eval_expr(expr.right, row)
            if expr.op == "AND": return left and right
            if expr.op == "OR": return left or right

        raise Exception(f"Unsupported expression type: {expr}")
    
    def _flip_join_condition(self, expr: Any) -> Any:
        if isinstance(expr, CompareExpr):
            return CompareExpr(field=expr.value, op=expr.op, value=expr.field)
        return expr
    
    def _eval_join(self, join: JoinRef, plan: List[str]) -> List[Dict]:
        left_rows = self._eval_from_source(join.left, plan)
        right_rows = self._eval_from_source(join.right, plan)

        plan.append(f"JOIN TYPE: {join.join_type}")

        # ------------------------------------------------------------
        # Determine join type: equality or IN-list
        # ------------------------------------------------------------
        on_expr = join.on

        # Case 1: Equality join: a.id = b.id
        if isinstance(on_expr, CompareExpr) and on_expr.op == "=":
            left_col = on_expr.field
            right_col = on_expr.value

            # Build hash table on right side
            hash_table = {}
            for r in right_rows:
                key = r.get(right_col.name)
                hash_table.setdefault(key, []).append(r)

            # Perform join
            results = []
            for L in left_rows:
                left_key = L.get(left_col.name)
                matches = hash_table.get(left_key)

                if matches:
                    for R in matches:
                        results.append(self._merge_rows(L, R))
                elif join.join_type == "LEFT":
                    results.append(self._merge_rows(L, {}))

                # Early LIMIT
                if self._limit_reached(results, plan):
                    return results

            return results

        # Case 2: IN-list join: a.id IN b.list_field
        if isinstance(on_expr, InListExpr):
            left_col = on_expr.field
            right_col = on_expr.list_field

            # Build hash table: normalized right_list_value -> right_row
            hash_table = {}
            for r in right_rows:
                lst = r.get(right_col.name)
#                for k, v in r.items():
#                    print(f"{k}: {v}")
                if isinstance(lst, list):
                    for item in lst:
#                        print("RIGHT_ITEM:", item, type(item))
                        # Normalize item to string
                        item = str(item)
                        hash_table.setdefault(item, []).append(r)
                                                
            results = []
            for L in left_rows:
                left_key = L.get(left_col.name)

#                print("LEFT_KEY:", left_key, type(left_key))

                # Normalize left_key to string
                left_key = str(left_key)

                matches = hash_table.get(left_key)
#                print(left_key)
#                for k, v in hash_table.items():
#                    print(f"{k}: {len(v)}")
#                print("Hash: ", hash_table.get(left_key))
#                sys.exit(0)

#                print("Matches: ", matches)

                if matches:
                    for R in matches:

#                        print("RIGHT_ITEM:", item, type(item))

                        results.append(self._merge_rows(L, R))
                elif join.join_type == "LEFT":
                    results.append(self._merge_rows(L, {}))

                if self._limit_reached(results, plan):
                    return results

            return results

        # Unsupported join type
        raise Exception("JOIN ON clause must be a simple equality or IN-list condition.")
    
    def _eval_join_condition(self, expr: Any, L: Dict, R: Dict) -> bool:
        return bool(self._eval_condition_expr(expr, L, R))


    def _eval_condition_expr(self, expr: Any, L: Dict, R: Dict) -> Any:
        # BinaryExpr (AND / OR)
        if isinstance(expr, BinaryExpr):
            left = self._eval_condition_expr(expr.left, L, R)
            right = self._eval_condition_expr(expr.right, L, R)
            if expr.op == "AND":
                return bool(left) and bool(right)
            if expr.op == "OR":
                return bool(left) or bool(right)

        # CompareExpr
        if isinstance(expr, CompareExpr):
            left = self._eval_value_expr(expr.field, L, R)
            right = self._eval_value_expr(expr.value, L, R)
            op = expr.op
            if op == "=":
                return left == right
            if op == "<":
                return left < right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            if op == ">=":
                return left >= right
            return False

        # Fallback: treat as truthy
        return bool(self._eval_value_expr(expr, L, R))
    
    def _eval_value_expr(self, expr: Any, L: Dict, R: Dict) -> Any:
        if isinstance(expr, str):
            if "." in expr:
                alias, field = expr.split(".", 1)
                # try left then right
                if alias in L and field in L:
                    return L[field]
                if alias in R and field in R:
                    return R[field]
                return None
            # unqualified
            if expr in L:
                return L[expr]
            if expr in R:
                return R[expr]
            return None

        if isinstance(expr, ArithExpr) or isinstance(expr, UnaryExpr) or isinstance(expr, FuncExpr):
            return None

        # Literal
        if isinstance(expr, (int, float)):
            return expr

        return None
    
    def _materialize_temp(self, rows: List[Dict]) -> str:
        temp_name = f"_tmp_join_{uuid.uuid4().hex[:8]}"
        coll = self.mongo.mm_database[temp_name]
        if rows:
            coll.insert_many(rows)
        return temp_name
    
    def _eval_table(self, table_ref: TableRef, plan: List[str]) -> List[Dict]:
        coll_name = table_ref.name
        plan.append(f"FROM TABLE: {coll_name}")
        coll = self.mongo.mm_database[coll_name]
        return list(coll.find({}))

    
    def _eval_table_alias(self, table_alias: TableAlias, plan: List[str]) -> List[Dict]:
        coll_name = table_alias.source.name
        plan.append(f"FROM TABLE: {coll_name} AS {table_alias.alias}")
        coll = self.mongo.mm_database[coll_name]
        return list(coll.find({}))

    def _eval_subquery(self, select: SelectStmt, plan: List[str]) -> str:
        temp_name = self._materialize_subquery(select, plan)
        plan.append(f"SUBQUERY MATERIALIZED AS: {temp_name}")
        coll = self.mongo.mm_database[temp_name]
        return list(coll.find({}))
    
    def _eval_compiled_expr(self, compiled, row):
        # Literal number
        if isinstance(compiled, (int, float)):
            return compiled

        # Simple field reference: "$title"
        if isinstance(compiled, str) and compiled.startswith("$"):
            field = compiled[1:]
            return row.get(field)

        # Arithmetic: {"$add": [left, right]}
        if isinstance(compiled, dict) and "$add" in compiled:
            left, right = compiled["$add"]
            return self._eval_compiled_expr(left, row) + self._eval_compiled_expr(right, row)

        if isinstance(compiled, dict) and "$subtract" in compiled:
            left, right = compiled["$subtract"]
            return self._eval_compiled_expr(left, row) - self._eval_compiled_expr(right, row)

        if isinstance(compiled, dict) and "$multiply" in compiled:
            left, right = compiled["$multiply"]
            return self._eval_compiled_expr(left, row) * self._eval_compiled_expr(right, row)

        if isinstance(compiled, dict) and "$divide" in compiled:
            left, right = compiled["$divide"]
            return self._eval_compiled_expr(left, row) / self._eval_compiled_expr(right, row)

        return None
    
    def _cleanup_temp_collections(self):
        db = self.mongo.mm_database
        if db is None:
            return

        for name in db.list_collection_names():
            if (
                name.startswith("_tmp_") or
                name.startswith("tmp_select_") or
                name.startswith("_sql_tmp_")
            ):
                try:
                    db.drop_collection(name)
                    _log_debug(f"Dropped temp collection: {name}")
                except Exception as e:
                    _log_debug(f"Failed to drop temp collection {name}: {e}")

    def _limit_reached(self, results, plan):
        for p in plan:
            if p.startswith("LIMIT"):
                try:
                    limit_val = int(p.split(":")[1].strip())
                    return len(results) >= limit_val
                except:
                    return False
        return False
    
    def _merge_rows(self, left_row: dict, right_row: dict) -> dict:
        """
        Merge two MongoDB result rows for JOIN operations.
        If the right row is empty (LEFT JOIN no match), return left row + empty right fields.
        """
        if not isinstance(left_row, dict):
            left_row = {}

        if not isinstance(right_row, dict):
            right_row = {}

        merged = {}

        # Copy left row fields
        for k, v in left_row.items():
            merged[k] = v

        # Copy right row fields, avoiding collisions
        for k, v in right_row.items():
            if k in merged:
                merged[f"right_{k}"] = v
            else:
                merged[k] = v

        return merged
    
    def _normalize_id(value):
        """
        Normalize a value so that ObjectId comparisons work correctly.
        Converts:
        - ObjectId -> string
        - 24-char hex strings -> string
        - "ObjectId('...')" -> string
        Leaves all other values unchanged.
        """

        # Case 1: Already an ObjectId
        if isinstance(value, ObjectId):
            return str(value)

        # Case 2: "ObjectId('abc123')" string
        if isinstance(value, str) and value.startswith("ObjectId("):
            try:
                inner = value.split("ObjectId(")[1].strip(")'\"")
                return inner
            except Exception:
                return value

        # Case 3: 24‑hex string (valid ObjectId)
        if isinstance(value, str) and len(value) == 24:
            try:
                ObjectId(value)  # validate
                return value
            except Exception:
                return value

        # Case 4: Lists (normalize each element)
        if isinstance(value, list):
            return [normalize_id(v) for v in value]

        # Case 5: Dicts (normalize values)
        if isinstance(value, dict):
            return {k: normalize_id(v) for k, v in value.items()}

        # Everything else unchanged
        return value
# ---
# END OF SQLToMongoTranslator
# ---
