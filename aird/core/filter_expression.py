"""Filter expression parser for complex search queries."""

import re


class FilterExpression:
    """Parse and evaluate complex filter expressions with AND/OR logic"""
    
    def __init__(self, expression: str):
        self.original_expression = expression
        self.parsed_expression = self._parse(expression)
    
    def _parse(self, expression: str):
        """Parse filter expression into evaluable structure"""
        if not expression or not expression.strip():
            return None
            
        expression = expression.strip()
        
        # Handle escaped expressions (prefix with backslash to force literal interpretation)
        if expression.startswith('\\'):
            return {'type': 'term', 'value': expression[1:].strip('"')}
        
        # Handle quoted expressions (always literal)
        if ((expression.startswith('"') and expression.endswith('"')) or 
            (expression.startswith("'") and expression.endswith("'"))):
            return {'type': 'term', 'value': expression[1:-1]}
        
        # Check if this looks like a logical expression
        # Use word boundary regex to detect standalone AND/OR operators
        has_logical_operators = (
            re.search(r'\bAND\b', expression, re.IGNORECASE) or
            re.search(r'\bOR\b', expression, re.IGNORECASE)
        )
        
        # Additional check: make sure these are actually surrounded by whitespace (logical operators)
        if has_logical_operators:
            # Verify these are standalone words, not part of other words
            and_matches = list(re.finditer(r'\bAND\b', expression, re.IGNORECASE))
            or_matches = list(re.finditer(r'\bOR\b', expression, re.IGNORECASE))
            
            has_logical_and = any(
                self._is_standalone_operator_static(expression, match.start(), match.end())
                for match in and_matches
            )
            has_logical_or = any(
                self._is_standalone_operator_static(expression, match.start(), match.end())
                for match in or_matches
            )
            
            has_logical_operators = has_logical_and or has_logical_or
        
        if not has_logical_operators:
            return {'type': 'term', 'value': expression.strip('"')}
        
        # Parse complex expressions
        return self._parse_complex(expression)
    
    def _parse_complex(self, expression: str):
        """Parse complex expressions with AND/OR and parentheses"""
        try:
            # Handle parentheses first by balancing them
            expression = expression.strip()
            
            # If the entire expression is wrapped in parentheses, remove them
            if expression.startswith('(') and expression.endswith(')'):
                # Check if parentheses are balanced
                if self._is_balanced_parentheses(expression):
                    return self._parse_complex(expression[1:-1])
            
            # Find OR outside of parentheses (lower precedence)
            or_parts = self._split_respecting_parentheses(expression, 'OR')
            if len(or_parts) > 1:
                return {
                    'type': 'or',
                    'operands': [self._parse_and_part(part.strip()) for part in or_parts]
                }
            
            # If no OR, try AND
            return self._parse_and_part(expression)
            
        except Exception:
            # Fallback to simple term matching on parse error
            return {'type': 'term', 'value': expression.strip('"')}
    
    def _parse_and_part(self, expression: str):
        """Parse AND expressions"""
        and_parts = self._split_respecting_parentheses(expression, 'AND')
        if len(and_parts) > 1:
            return {
                'type': 'and',
                'operands': [self._parse_term(part.strip()) for part in and_parts]
            }
        return self._parse_term(expression.strip())
    
    def _parse_term(self, term: str):
        """Parse individual terms, handling quotes and parentheses"""
        term = term.strip()
        
        # Handle parentheses
        if term.startswith('(') and term.endswith(')'):
            return self._parse_complex(term[1:-1])
        
        # Handle quoted terms
        if (term.startswith('"') and term.endswith('"')) or (term.startswith("'") and term.endswith("'")):
            return {'type': 'term', 'value': term[1:-1]}
        
        return {'type': 'term', 'value': term}
    
    def matches(self, line: str) -> bool:
        """Evaluate if a line matches the filter expression"""
        if self.parsed_expression is None:
            return True
        return self._evaluate(self.parsed_expression, line)
    
    def _evaluate(self, node, line: str) -> bool:
        """Recursively evaluate parsed expression against line"""
        if node['type'] == 'term':
            return node['value'].lower() in line.lower()
        elif node['type'] == 'and':
            return all(self._evaluate(operand, line) for operand in node['operands'])
        elif node['type'] == 'or':
            return any(self._evaluate(operand, line) for operand in node['operands'])
        return False
    
    def _split_respecting_parentheses(self, expression: str, operator: str):
        """Split expression by operator while respecting parentheses and word boundaries"""
        parts = []
        current_part = ""
        paren_depth = 0
        in_quotes = False
        quote_char = None
        i = 0
        
        while i < len(expression):
            char = expression[i]
            
            # Handle quotes
            if char in ['"', "'"] and not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
            
            # Skip everything inside quotes
            if in_quotes:
                current_part += char
                i += 1
                continue
            
            # Handle parentheses
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
            
            # Check for operator when we're at the top level
            if paren_depth == 0:
                # Check if we're at the start of the operator
                remaining = expression[i:]
                # Pattern: operator with word boundaries, possibly with whitespace
                op_pattern = f'\\b{re.escape(operator)}\\b'
                match = re.match(op_pattern, remaining, re.IGNORECASE)
                if match:
                    # Verify this is actually a logical operator by checking context
                    operator_start = i
                    operator_end = i + len(match.group(0))
                    
                    # Check if surrounded by whitespace or start/end of string
                    before_ok = operator_start == 0 or expression[operator_start - 1].isspace()
                    after_ok = operator_end >= len(expression) or expression[operator_end].isspace()
                    
                    if before_ok and after_ok:
                        # Found operator at top level
                        parts.append(current_part.strip())
                        current_part = ""
                        # Skip the operator and any following whitespace
                        i = operator_end
                        while i < len(expression) and expression[i].isspace():
                            i += 1
                        continue
            
            current_part += char
            i += 1
        
        if current_part.strip():
            parts.append(current_part.strip())
        
        return parts if len(parts) > 1 else [expression]
    
    def _is_balanced_parentheses(self, expression: str):
        """Check if parentheses are balanced"""
        depth = 0
        in_quotes = False
        quote_char = None
        
        for char in expression:
            if char in ['"', "'"] and not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
            elif not in_quotes:
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth < 0:
                        return False
        
        return depth == 0
    
    def _is_standalone_operator(self, expression: str, start: int, end: int, operator: str):
        """Check if AND/OR at this position is a standalone logical operator"""
        return self._is_standalone_operator_static(expression, start, end)
    
    @staticmethod
    def _is_standalone_operator_static(expression: str, start: int, end: int):
        """Static version of _is_standalone_operator for use during parsing"""
        # Check if surrounded by whitespace (indicating it's a logical operator)
        before_space = start == 0 or expression[start - 1].isspace()
        after_space = end >= len(expression) or expression[end].isspace()
        
        return before_space and after_space

    def __str__(self):
        return f"FilterExpression({self.original_expression})"
