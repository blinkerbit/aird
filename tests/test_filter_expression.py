"""Tests for aird/core/filter_expression.py"""

import pytest
from aird.core.filter_expression import FilterExpression


class TestFilterExpressionBasic:
    """Basic tests for FilterExpression class"""
    
    def test_empty_expression_matches_all(self):
        """Test that empty expression matches everything"""
        fe = FilterExpression("")
        assert fe.matches("any line") is True
        assert fe.matches("") is True
    
    def test_whitespace_expression_matches_all(self):
        """Test that whitespace-only expression matches everything"""
        fe = FilterExpression("   ")
        assert fe.matches("any line") is True
    
    def test_none_expression(self):
        """Test handling None-like expressions"""
        fe = FilterExpression("")
        assert fe.parsed_expression is None
        assert fe.matches("anything") is True
    
    def test_simple_term_matching(self):
        """Test simple term matching"""
        fe = FilterExpression("hello")
        assert fe.matches("hello world") is True
        assert fe.matches("HELLO WORLD") is True  # case insensitive
        assert fe.matches("goodbye") is False
    
    def test_case_insensitive_matching(self):
        """Test that matching is case insensitive"""
        fe = FilterExpression("ERROR")
        assert fe.matches("error occurred") is True
        assert fe.matches("Error: something") is True
        assert fe.matches("An ERROR happened") is True


class TestFilterExpressionQuoted:
    """Tests for quoted expressions"""
    
    def test_double_quoted_expression(self):
        """Test double-quoted literal expression"""
        fe = FilterExpression('"hello world"')
        assert fe.matches("hello world") is True
        assert fe.matches("helloworld") is False
    
    def test_single_quoted_expression(self):
        """Test single-quoted literal expression"""
        fe = FilterExpression("'hello world'")
        assert fe.matches("hello world") is True
        assert fe.matches("helloworld") is False
    
    def test_escaped_expression(self):
        """Test escaped expression (backslash prefix)"""
        fe = FilterExpression("\\hello AND world")
        # Should be treated as literal "hello AND world"
        assert fe.matches("hello AND world") is True


class TestFilterExpressionLogicalOperators:
    """Tests for AND/OR logical operators"""
    
    def test_and_operator(self):
        """Test AND operator"""
        fe = FilterExpression("hello AND world")
        assert fe.matches("hello world") is True
        assert fe.matches("hello there world") is True
        assert fe.matches("hello") is False
        assert fe.matches("world") is False
    
    def test_or_operator(self):
        """Test OR operator"""
        fe = FilterExpression("hello OR world")
        assert fe.matches("hello") is True
        assert fe.matches("world") is True
        assert fe.matches("hello world") is True
        assert fe.matches("goodbye") is False
    
    def test_and_case_insensitive(self):
        """Test AND operator is case insensitive"""
        fe = FilterExpression("hello and world")
        assert fe.matches("hello world") is True
        
        fe2 = FilterExpression("hello AND world")
        assert fe2.matches("hello world") is True
    
    def test_or_case_insensitive(self):
        """Test OR operator is case insensitive"""
        fe = FilterExpression("hello or world")
        assert fe.matches("hello") is True
        
        fe2 = FilterExpression("hello OR world")
        assert fe2.matches("hello") is True
    
    def test_multiple_and_operators(self):
        """Test multiple AND operators"""
        fe = FilterExpression("a AND b AND c")
        assert fe.matches("a b c") is True
        assert fe.matches("c b a") is True
        assert fe.matches("a b") is False
    
    def test_multiple_or_operators(self):
        """Test multiple OR operators"""
        fe = FilterExpression("a OR b OR c")
        assert fe.matches("a") is True
        assert fe.matches("b") is True
        assert fe.matches("c") is True
        assert fe.matches("d") is False
    
    def test_mixed_and_or_precedence(self):
        """Test that OR has lower precedence than AND"""
        # "a AND b OR c" should be "(a AND b) OR c"
        fe = FilterExpression("a AND b OR c")
        assert fe.matches("a b") is True  # a AND b
        assert fe.matches("c") is True     # OR c
        assert fe.matches("a") is False    # just a, not enough
        assert fe.matches("b") is False    # just b, not enough


class TestFilterExpressionParentheses:
    """Tests for parenthesized expressions"""
    
    def test_simple_parentheses(self):
        """Test simple parenthesized expression - parentheses only work with logical operators"""
        # Without logical operators, parentheses are treated as literal characters
        fe = FilterExpression("(hello)")
        assert fe.matches("(hello)") is True  # matches literal "(hello)"
        assert fe.matches("hello") is False   # doesn't match without parens
        
        # With a logical operator, parentheses work for grouping
        fe2 = FilterExpression("(hello) AND world")
        assert fe2.matches("hello world") is True
        assert fe2.matches("(hello) world") is True
    
    def test_nested_parentheses_and(self):
        """Test nested parentheses with AND"""
        fe = FilterExpression("(a AND b)")
        assert fe.matches("a b") is True
        assert fe.matches("a") is False
    
    def test_nested_parentheses_or(self):
        """Test nested parentheses with OR"""
        fe = FilterExpression("(a OR b)")
        assert fe.matches("a") is True
        assert fe.matches("b") is True
        assert fe.matches("c") is False
    
    def test_complex_parentheses(self):
        """Test complex parenthesized expression"""
        # "(a OR b) AND c" - either a or b, and must have c
        fe = FilterExpression("(a OR b) AND c")
        assert fe.matches("a c") is True
        assert fe.matches("b c") is True
        assert fe.matches("a b c") is True
        assert fe.matches("a") is False
        assert fe.matches("c") is False


class TestFilterExpressionEdgeCases:
    """Edge case tests for FilterExpression"""
    
    def test_word_with_and_in_it(self):
        """Test that words containing AND are not split"""
        fe = FilterExpression("android")
        assert fe.matches("android phone") is True
        # "android" contains "and" but should be treated as single term
    
    def test_word_with_or_in_it(self):
        """Test that words containing OR are not split"""
        fe = FilterExpression("order")
        assert fe.matches("order placed") is True
        # "order" contains "or" but should be treated as single term
    
    def test_str_representation(self):
        """Test __str__ representation"""
        fe = FilterExpression("test expression")
        assert "test expression" in str(fe)
    
    def test_balanced_parentheses(self):
        """Test balanced parentheses detection"""
        fe = FilterExpression("((a AND b))")
        assert fe.matches("a b") is True
    
    def test_quotes_in_split(self):
        """Test that operators inside quotes are not recognized"""
        fe = FilterExpression('"a AND b"')
        # Should be treated as literal "a AND b"
        assert fe.matches("a AND b") is True
        assert fe.matches("a b") is False  # should not match without AND


class TestFilterExpressionInternalMethods:
    """Tests for internal methods of FilterExpression"""
    
    def test_is_balanced_parentheses_simple(self):
        """Test _is_balanced_parentheses with simple cases"""
        fe = FilterExpression("")
        assert fe._is_balanced_parentheses("()") is True
        assert fe._is_balanced_parentheses("(())") is True
        assert fe._is_balanced_parentheses("(()") is False
        assert fe._is_balanced_parentheses("())") is False
    
    def test_is_balanced_parentheses_with_quotes(self):
        """Test _is_balanced_parentheses ignores parens in quotes"""
        fe = FilterExpression("")
        assert fe._is_balanced_parentheses('"("') is True
        assert fe._is_balanced_parentheses("')'") is True
    
    def test_is_standalone_operator_static(self):
        """Test _is_standalone_operator_static method"""
        # Test at start of string
        result = FilterExpression._is_standalone_operator_static("AND test", 0, 3)
        assert result is True
        
        # Test in middle with spaces
        result = FilterExpression._is_standalone_operator_static("a AND b", 2, 5)
        assert result is True
        
        # Test without proper spacing
        result = FilterExpression._is_standalone_operator_static("aANDb", 1, 4)
        assert result is False
    
    def test_parse_term_quoted(self):
        """Test _parse_term with quoted terms"""
        fe = FilterExpression("")
        result = fe._parse_term('"test"')
        assert result == {'type': 'term', 'value': 'test'}
        
        result = fe._parse_term("'test'")
        assert result == {'type': 'term', 'value': 'test'}
    
    def test_parse_term_unquoted(self):
        """Test _parse_term with unquoted terms"""
        fe = FilterExpression("")
        result = fe._parse_term('test')
        assert result == {'type': 'term', 'value': 'test'}


class TestFilterExpressionSplitting:
    """Tests for expression splitting logic"""
    
    def test_split_respecting_parentheses(self):
        """Test _split_respecting_parentheses method"""
        fe = FilterExpression("")
        
        # Simple OR split
        result = fe._split_respecting_parentheses("a OR b", "OR")
        assert len(result) == 2
        assert "a" in result[0]
        assert "b" in result[1]
    
    def test_split_respecting_quotes(self):
        """Test that splitting respects quoted strings"""
        fe = FilterExpression("")
        
        # OR inside quotes should not split
        result = fe._split_respecting_parentheses('"a OR b"', "OR")
        assert len(result) == 1
    
    def test_split_respecting_nested_parens(self):
        """Test that splitting respects nested parentheses"""
        fe = FilterExpression("")
        
        # OR inside parens should not split at top level
        result = fe._split_respecting_parentheses("(a OR b) AND c", "AND")
        assert len(result) == 2


class TestFilterExpressionRealWorldExamples:
    """Real-world usage examples for FilterExpression"""
    
    def test_log_filtering_error_and_warning(self):
        """Test filtering logs for errors or warnings"""
        fe = FilterExpression("error OR warning")
        assert fe.matches("[ERROR] Something went wrong") is True
        assert fe.matches("[WARNING] Be careful") is True
        assert fe.matches("[INFO] All good") is False
    
    def test_log_filtering_specific_error(self):
        """Test filtering for specific error type"""
        fe = FilterExpression("error AND database")
        assert fe.matches("Database connection error occurred") is True
        assert fe.matches("File not found error") is False
    
    def test_complex_log_filter(self):
        """Test complex log filtering"""
        fe = FilterExpression("(error OR exception) AND NOT timeout")
        # This would require NOT support, which doesn't exist
        # Just test without NOT
        fe = FilterExpression("(error OR exception) AND database")
        assert fe.matches("Database exception thrown") is True
        assert fe.matches("Database error logged") is True
        assert fe.matches("Database timeout") is False
