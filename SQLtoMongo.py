# ---
# SQLToMongoTranslator
# ---
#
# This class provides translation from SQL-style statements into MongoDB
# operations compatible with the MongoManager class.  A normalization
# routine is used to uppercase SQL keywords while preserving quoted text
# and identifiers.  This allows case-insensitive SQL parsing without
# corrupting MongoDB field names or string values.
#
# Rather than trying to deal with the entire SQL statement at once, a algorithm that 
# performs keyword-scanning is used where each SQL clause (SELECT, FROM, WHERE, ORDER BY,
# LIMIT, OFFSET) is located by scanning the normalized SQL text for known keywords while ignoring
# things like quoted strings, variable names, and other non-keyword elements.  The text that make up 
# each clause is then extracted based on the positions of these keywords, then passed to small,
# more specific mini-parsers.  Once the parsing of each segment of a SQL statement is completed, 
# the results are combined into a single MongoDB operation dictionary that can be executed by the 
# MongoManager.  This approach allows for a more modular, less complicated parsing approach.
#
# Supported features:
#    - SELECT with WHERE, ORDER BY, LIMIT, OFFSET
#    - AND / OR conditions
#    - LIKE
#    - DISTINCT
#    - COUNT(), SUM(), AVG()
#    - INSERT, UPDATE, DELETE
#
# ---
# Mark Woodford
# SNHU CS499 Computer Science Capstone
# May 2026
# ---
# version 1.0
#---

import re


class SQLToMongoTranslator:

    # ---
    # Initialization
    # ---
    # Here we just set up some initial routine translations.  The methods that handle each of the
    #    noted SQL commands are associated with the keyword they manage.
    def __init__(self):
        # SQL command dispatch
        self.command_map = {
            "SELECT": self._parse_select,
            "INSERT": self._parse_insert,
            "UPDATE": self._parse_update,
            "DELETE": self._parse_delete
        }

        # Aggregate functions -- method mappings for the functions that require Mongo aggregation.
        self.agg_map = {
            "COUNT": self._agg_count,
            "SUM": self._agg_sum,
            "AVG": self._agg_avg,
            "MIN": self._agg_min,
            "MAX": self._agg_max
        }

        # SQL keywords that modify the way a primary method functions.  Since we uppercase the
        #    SQL statement before any operations are called we don't need to map lowercase versions.
        self.sql_keywords = [
            "SELECT", "FROM", "WHERE", "ORDER", "BY",
            "LIMIT", "OFFSET", "INSERT", "INTO", "VALUES",
            "UPDATE", "SET", "DELETE", "DISTINCT",
            "AND", "OR", "LIKE"
        ]

        # This list is the clause keywords in the order they appear in SQL SELECT statements.
        self.clause_keywords = [
            "SELECT",
            "FROM",
            "WHERE",
            "ORDER BY",
            "LIMIT",
            "OFFSET"
        ]

    # ---
    # SQL Entry Point
    # ---
    # This is where the class is entered by programs that use it.  The translate_sql method will
    #    handle the entire SQL statement entered.  From here it will normalize the statement, 
    #    capitalize the SQL words while preserving variables and quoted strings, then transfer to
    #    the appropriate method based on the first SQL keyword it finds.
    def translate_sql(self, sql_text):
        sql_text = sql_text.strip()

        # Remove trailing semicolon
        if sql_text.endswith(";"):
            sql_text = sql_text[:-1].strip()

        # Normalize SQL for case-insensitive parsing.  This isn't a matter of just uppercasing the entire statement, 
        #    we need to preserve quoted strings and any variables as they are.
        normalized = self._normalize_sql(sql_text)

        # In SQL, the main function will be the first word in the command string.
        first_word = normalized.split()[0].upper()

        if first_word in self.command_map:
            return self.command_map[first_word](normalized, sql_text)

        # If we manage to get here, the first word located is not a supported SQL command.
        raise Exception("ERROR: Unsupported SQL command.")

    # ---
    # SQL Normalizer
    # ---
    # This method preserves quoted strings exactly as they appear by extracting
    # the quoted text as a whole chunk, never splitting or altering
    # the contents.  Only unquoted segments are scanned for SQL keywords.
    # ---
    def _normalize_sql(self, sql_text):
        result_segments = []
        current_text = ""
        in_single = False
        in_double = False

        i = 0
        while i < len(sql_text):
            ch = sql_text[i]

            # Detect and enter or exit single-quoted string.  Here we'll just look for quotes and ensure that
            #    we keep track of whether we're inside a quoted string or not.  If we encounter a
            #    quote and we're not already in the opposite type of quote, we know we're starting
            #    or ending a quoted segment, so nested quotes don't present an issue.
            if ch == "'" and not in_double:
                if current_text != "":
                    result_segments.append(("unquoted", current_text))
                    current_text = ""
                in_single = not in_single
                quoted = "'"
                i += 1
                while i < len(sql_text):
                    quoted += sql_text[i]
                    if sql_text[i] == "'" and sql_text[i-1] != "\\":
                        break
                    i += 1
                result_segments.append(("quoted", quoted))
                i += 1
                continue

            # Detect and enter or exit double-quoted string
            if ch == '"' and not in_single:
                if current_text != "":
                    result_segments.append(("unquoted", current_text))
                    current_text = ""
                in_double = not in_double
                quoted = '"'
                i += 1
                while i < len(sql_text):
                    quoted += sql_text[i]
                    if sql_text[i] == '"' and sql_text[i-1] != "\\":
                        break
                    i += 1
                result_segments.append(("quoted", quoted))
                i += 1
                continue

            # Inside quotes get appended literally to the current_text 
            if in_single or in_double:
                current_text += ch
                i += 1
                continue

            # Outside quotes → accumulate for keyword detection
            current_text += ch
            i += 1

        # Append the final text segment
        if current_text != "":
            result_segments.append(("unquoted", current_text))

        # Uppercase keywords in unquoted segments only
        final_text = ""
        for seg_type, text in result_segments:
            if seg_type == "quoted":
                final_text += text
            else:
                final_text += self._uppercase_keywords(text)

        return final_text

    # ---
    # Uppercase SQL keywords in a segment
    # ---
    # Here we'll look for keywords in unquoted section and conver them to uppercase, 
    #    leaving non-keyword text as is.  This allows us to do case-insensitive parsing while preserving
    #    field/variable names and string values exactly as they appear.
    def _uppercase_keywords(self, segment):
        keywords = segment.split()
        new_keywords = []

        for kw in keywords:
            upper_kw = kw.upper()
            if upper_kw in self.sql_keywords:
                new_keywords.append(upper_kw)
            else:
                new_keywords.append(kw)

        return " ".join(new_keywords)

    # ---
    # Keyword Scanner -- find the clause keywords in the normalized SQL string and extract the text 
    #    that belongs to each clause.  We then keep track of positions so that the accompanying text 
    #    can be extracted and sent to the appropriate mini-parsers.  
    # ---
    def _extract_clauses(self, normalized_sql):
        sql_upper = normalized_sql.upper()
        positions = {}

        # Find keyword positions and store them in a dictionary.
        for kw in self.clause_keywords:
            pos = sql_upper.find(kw)
            if pos != -1:
                positions[kw] = pos

        # Sort by position.  This is important because we need to know the order of the clauses 
        #    to extract the correct text for each.
        ordered = []
        for kw in self.clause_keywords:
            if kw in positions:
                ordered.append((kw, positions[kw]))

        # Here we keep track of the text between boundaries, keeping the text entries in a
        #    dictionary with the clause keyword as the key.  This allows us to easily pass the 
        #    right text to the right mini-parser.
        clauses = {}
        for i in range(len(ordered)):
            kw, pos = ordered[i]
            start = pos + len(kw)
            if i + 1 < len(ordered):
                next_kw, next_pos = ordered[i + 1]
                end = next_pos
            else:
                end = len(normalized_sql)

            clause_text = normalized_sql[start:end].strip()
            clauses[kw] = clause_text

        return clauses

    # ---
    # SELECT Translation
    # ---
    # This is the parser for SELECT statements.  It will handle the various clauses that may be
    #    present in a SELECT statement, including WHERE, ORDER BY, LIMIT, OFFSET, and DISTINCT.  
    #    It also checks for aggregate functions in the SELECT fields and calls any aggregate parser
    #    as necessary.
    def _parse_select(self, normalized, original):
        clauses = self._extract_clauses(normalized)

        select_chunk = clauses.get("SELECT", "")
        from_chunk = clauses.get("FROM", "")
        where_chunk = clauses.get("WHERE", "")
        order_chunk = clauses.get("ORDER BY", "")
        limit_chunk = clauses.get("LIMIT", "")
        offset_chunk = clauses.get("OFFSET", "")

        # This code will deal with a DISTINCT if encountered.  We set a flag to indicate 
        #    that we need to do a distinct operation, then remove the DISTINCT keyword from the 
        #    select_chunk so that the rest of the parsing can proceed as normal.
        is_distinct = False
        if select_chunk.upper().startswith("DISTINCT"):
            is_distinct = True
            select_chunk = select_chunk[8:].strip()

        fields = []
        for f in select_chunk.split(","):
            f = f.strip()
            fields.append(f)

        # Here we detect and deal with any aggregate functions that show up.  If one is found
        #    we dispatch to the appropriate aggregate parser to get the result immediately since
        #    aggregate operations are handled differently than normal command processing.
        if self._is_agg(select_chunk):
            return self._parse_agg(select_chunk, from_chunk, where_chunk)

        # Here the DISTINCT flag is check and, if set, we return a read operation with the 
        #    distinct field specified.  The rest of the parsing is still done to ensure that any
        #    conditions are applied to the distinct query, but the projection, sort, limit, and offset
        #    are not relevant to DISTINCT so they are set to None.
        if is_distinct:
            return {
                "operation": "read",
                "collection": from_chunk,
                "filter": self._parse_where(where_chunk),
                "projection": None,
                "sort": None,
                "limit": None,
                "offset": None,
                "distinct": fields[0]
            }

        # There is no DISTINCT to deal withj, so we proceed with normal processing.  Projection is the
        #    MongoDB term for the fields we want to return, so we build a projection dictionary based on
        #    the fields given after SELECT but before any other clauses and treat them as fields.
        projection = None
        if fields != ["*"]:
            projection = {}
            for f in fields:
                projection[f] = 1

        # We've accounted for the fields that we care about, and have reached a WHERE clause (if 
        #    present) that needs to be parsed into a MongoDB filter.  We'll call the _parse_where 
        #    method to handle this.
        query_filter = self._parse_where(where_chunk)

        # Where is dealt with, so now we'll deal with ORDER BY if there is any.  This is a 
        #    matter of splitting the fields given in the ORDER BY clause and checking for any
        #    keyword set to determine the sort direction.  The resulting sort dictionary is built
        #    with field names as keys and 1 or -1 as values to indicate ascending or descending.
        sort_dict = None
        if order_chunk != "":
            sort_dict = {}
            parts = order_chunk.split(",")
            for piece in parts:
                piece = piece.strip()
                if piece == "":
                    continue
                tokens = piece.split()
                field = tokens[0]
                direction = 1
                if len(tokens) > 1 and tokens[1].upper() == "DESC":
                    direction = -1
                sort_dict[field] = direction

        # LIMIT clauses are fairly straightforward, we just need to check if the limit value is a 
        #    digit and convert it to an integer if so.  If not, we can ignore it since it's not valid.
        limit_val = None
        if limit_chunk.isdigit():
            limit_val = int(limit_chunk)

        # An OFFSET clause is similar to LIMIT, but specifies the number of documents to skip rather
        #    than the number to include.
        offset_val = None
        if offset_chunk.isdigit():
            offset_val = int(offset_chunk)

        return {
            "operation": "read",
            "collection": from_chunk,
            "filter": query_filter,
            "projection": projection,
            "sort": sort_dict,
            "limit": limit_val,
            "offset": offset_val
        }

    # ---
    # Aggregation handling dispatch
    # ---
    # This method checks if the field specified in the SELECT clause is an aggregate function and,
    #    if so, dispatches to the appropriate mini-parser.

    def _is_agg(self, field_raw):
        func = field_raw.split("(")[0].upper()
        return func in self.agg_map

    def _parse_agg(self, field_raw, collection, where_chunk):
        func = field_raw.split("(")[0].upper()
        return self.agg_map[func](field_raw, collection, where_chunk)

    # ---
    # COUNT(), SUM(), AVG()
    # ---
    # These methods handle the aggregation functions associated with SQL COUNT, SUM, and AVG.
    #    They build a mongo aggregation pipeline that includes a $match stage based on the WHERE
    #    clause and then the appropriate aggregation stage 
    def _agg_count(self, field_raw, collection, where_chunk):
        return {
            "operation": "aggregate",
            "collection": collection,
            "pipeline": [
                {"$match": self._parse_where(where_chunk)},
                {"$count": "count"}
            ]
        }

    def _agg_sum(self, field_raw, collection, where_chunk):
        inner = field_raw[4:-1].strip()
        return {
            "operation": "aggregate",
            "collection": collection,
            "pipeline": [
                {"$match": self._parse_where(where_chunk)},
                {"$group": {"_id": None, "sum": {"$sum": f"${inner}"}}}
            ]
        }

    def _agg_avg(self, field_raw, collection, where_chunk):
        inner = field_raw[4:-1].strip()
        return {
            "operation": "aggregate",
            "collection": collection,
            "pipeline": [
                {"$match": self._parse_where(where_chunk)},
                {"$group": {"_id": None, "avg": {"$avg": f"${inner}"}}}
            ]
        }

    def _agg_min(self, field_raw, collection, where_chunk):
        inner = field_raw[4:-1].strip()
        return {
            "operation": "aggregate",
            "collection": collection,
            "pipeline": [
                {"$match": self._parse_where(where_chunk)},
                {"$group": {"_id": None, "min": {"$min": f"${inner}"}}}
            ]
        }

    def _agg_max(self, field_raw, collection, where_chunk):
        inner = field_raw[4:-1].strip()
        return {
            "operation": "aggregate",
            "collection": collection,
            "pipeline": [
                {"$match": self._parse_where(where_chunk)},
                {"$group": {"_id": None, "max": {"$max": f"${inner}"}}}
            ]
        }



    # ---
    # INSERT Translation
    # ---
    # Initially the SQL translator only dealt with SELECT, but as it turns out INSERT, UPDATE,
    #    and DELETE statements are comparatively fairly easy to handle once SELECT has an established
    #    method to use as a reference.  The parsing approach is similar to SELECT where we look for
    #    keywords and extract the relevant text, but since these statements are less complex
    #    have fewer clauses, the parsing is more direct.
    # ---
    def _parse_insert(self, normalized, original):
        post_into = normalized.split("INTO")[1].strip()
        collection = post_into.split("(")[0].strip()

        field_chunk = normalized.split("(")[1].split(")")[0]
        fields = []
        for f in field_chunk.split(","):
            fields.append(f.strip())

        value_chunk = normalized.split("VALUES")[1]
        value_chunk = value_chunk.replace("(", "").replace(")", "")
        insert_vals = []
        for v in value_chunk.split(","):
            insert_vals.append(v.strip())
        values = []
        for v in insert_vals:
            if v.startswith("'") or v.startswith('"'):
                values.append(v[1:-1])
            else:
                try:
                    values.append(int(v))
                except:
                    values.append(v)

        # Mongo doesn't deal with records in the SQL sense, it deals with documents.  We build 
        #    a document dictionary where the keys are the field names given in the SQL statement and the 
        #    values are the values that we stored above. This document is then included in the
        #    final operation dictionary that is returned.
        document = {}
        for i in range(len(fields)):
            document[fields[i]] = values[i]

        return {
            "operation": "create",
            "collection": collection,
            "document": document
        }

    # ---
    # UPDATE Translation
    # ---
    # An UPDATE is a bit more complicated than an INSERT since it can include a WHERE clause used to
    #    determine which documents are getting an update.  The associated SET clause can include 
    #    multiple field assignments, much like an INSERT.  Parsing it is similar to dealing with SELECT
    #    where we look for keywords and extract the relevant text, except UPDATE has fewer clauses to
    #    deal with.
    def _parse_update(self, normalized, original):
        post_set = normalized.split("SET")
        collection = post_set[0].replace("UPDATE", "").strip()

        set_chunk = post_set[1].strip()

        where_chunk = ""
        if " WHERE " in normalized.upper():
            where_pos = normalized.upper().index(" WHERE ")
            set_clause = normalized[normalized.upper().index("SET") + 3:where_pos].strip()
            where_chunk = normalized[where_pos + 7:].strip()
        else:
            set_clause = set_chunk

        # Now we'll set up the fields that need updating based on the SET clause.  This is a
        #    matter of splitting the clause by commas to get each assignment, then splitting 
        #    each assignment by the equals sign to get the field and value.  The field is 
        #    stripped of whitespace and used as a key in the update_fields dictionary, while 
        #    the value is processed to determine if it's a string or number and then stored as the
        #    value in the dictionary.
        update_fields = {}
        assignments = set_clause.split(",")
        for assign in assignments:
            field, value = assign.split("=")
            field = field.strip()
            value = value.strip()

            if value.startswith("'") or value.startswith('"'):
                update_fields[field] = value[1:-1]
            else:
                try:
                    update_fields[field] = int(value)
                except:
                    update_fields[field] = value

        query_filter = self._parse_where(where_chunk)

        return {
            "operation": "update",
            "collection": collection,
            "filter": query_filter,
            "update": update_fields
        }

    # ---
    # DELETE Translation
    # ---
    # A DELETE statement is similar to an UPDATE in that it can include a WHERE clause to determine
    #    which documents are supposed to be deleted, but it doesn't have a SET clause since we're
    #    not updating anything, just removing documents.  The parsing is similar to UPDATE where we
    #    look for keywords and extract the relevant text, but documents are simply removed after that.
    def _parse_delete(self, normalized, original):
        post_from = normalized.split("FROM")[1].strip()

        where_chunk = ""
        if " WHERE " in normalized.upper():
            where_pos = normalized.upper().index(" WHERE ")
            collection = normalized[normalized.upper().index("FROM") + 4:where_pos].strip()
            where_chunk = normalized[where_pos + 7:].strip()
        else:
            collection = post_from.strip()

        query_filter = self._parse_where(where_chunk)

        return {
            "operation": "delete",
            "collection": collection,
            "filter": query_filter
        }

    # ---
    # WHERE Parsing (AND, OR, LIKE, simple conditions)
    # ---
    def _parse_where(self, where_chunk):
        where_chunk = where_chunk.strip()
        if where_chunk == "":
            return {}

        # OR
        if " OR " in where_chunk.upper():
            parts = re.split(r"\s+OR\s+", where_chunk, flags=re.IGNORECASE)
            conds = []
            for p in parts:
                conds.append(self._parse_cond(p))
            return {"$or": conds}

        # AND
        if " AND " in where_chunk.upper():
            parts = re.split(r"\s+AND\s+", where_chunk, flags=re.IGNORECASE)
            conds = []
            for p in parts:
                conds.append(self._parse_cond(p))
            return {"$and": conds}

        return self._parse_cond(where_chunk)

    # ---
    # Parse a test condition.  Supported conditions are simple comparisons that use =, <, >, <=, >=,
    #    or LIKE for pattern matching.  The condition is parsed into a MongoDB filter and returned
    #    as a dictionary. 
    # ---
    def _parse_cond(self,cond_chunk):
        cond_chunk =cond_chunk.strip()

        # LIKE → regex
        if "LIKE" in cond_chunk.upper():
            parts = re.split(r"\s*LIKE\s*", cond_chunk, flags=re.IGNORECASE)
            field = parts[0].strip()
            value = parts[1].strip().strip("'\"")
            return {field: {"$regex": value, "$options": "i"}}

        ops = ["<=", ">=", "=", "<", ">"]

        for op in ops:
            if op in cond_chunk:
                parts = cond_chunk.split(op)
                field = parts[0].strip()
                value = parts[1].strip()

                if value.startswith("'") or value.startswith('"'):
                    value = value[1:-1]
                else:
                    try:
                        value = int(value)
                    except:
                        pass

                if op == "=":
                    return {field: value}
                if op == ">":
                    return {field: {"$gt": value}}
                if op == "<":
                    return {field: {"$lt": value}}
                if op == ">=":
                    return {field: {"$gte": value}}
                if op == "<=":
                    return {field: {"$lte": value}}

        return {}
