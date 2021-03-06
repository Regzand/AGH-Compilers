""" Type checker module """
from typing import Tuple, List

from compiler.parser import AST
from compiler.types import MType
from compiler.types.operations import OPERATIONS
from compiler.utils import SymbolTable, CompilerError, method_dispatch


class TypeChecker:

    def __init__(self, symbol_table: SymbolTable = SymbolTable(), collect_errors: bool = False):
        self.symbol_table = symbol_table
        self.collect_errors = collect_errors
        self.errors = []

    def _error(self, line_span: Tuple[int, int], message: str):
        err = TypeCheckerError(line_span, message)

        if not self.collect_errors:
            raise err

        self.errors.append(err)

    @method_dispatch
    def check(self, node: AST.Node) -> MType:
        raise NotImplementedError(f'There is no type check implemented for {node.__class__}')

    # ==============================================
    #   EXPRESSIONS
    # ==============================================
    @check.register
    def _(self, node: AST.ConstantExpression) -> MType:
        return MType(type(node.value))

    @check.register
    def _(self, node: AST.VectorExpression) -> MType:

        # if its empty vector
        if len(node.expressions) == 0:
            return MType.EMPTY_VECTOR

        # check for consistent types
        types = list({self.check(e) for e in node.expressions})
        if len(types) > 1:
            self._error(node.line_span, f'All elements in vector have to have the same type, {len(types)} different types found')

        # increase shape
        return MType(types[0].type, (len(node.expressions), ) + types[0].shape)

    @check.register
    def _(self, node: AST.RangeExpression) -> MType:

        # check if begin and end are integers
        if self.check(node.begin) != MType.INT:
            self._error(node.line_span, 'First element in range has to be a integer')
        if self.check(node.end) != MType.INT:
            self._error(node.line_span, 'Second element in range has to be a integer')

        return MType.INT

    @check.register
    def _(self, node: AST.InstructionStatement) -> MType:

        # for BREAK and CONTINUE check scope
        if node.name in ['break', 'continue']:
            if not self.symbol_table.has_scope('loop'):
                self._error(node.line_span, f'Instruction {node.name} has to be inside a loop')

        # check types of arguments
        for a in node.arguments:
            self.check(a)

        return MType.NONE

    def _check_normal_operators(self, node: AST.OperatorExpression, types: List[MType]) -> MType:
        types = tuple(types)

        # check if operator is applicable for these types
        if types not in OPERATIONS[node.operator]:
            self._error(node.line_span, f'Operator {node.operator} not applicable for types {types}')
            return MType.NONE

        # return type of result
        return OPERATIONS[node.operator][types]

    def _check_equality_operators(self, node: AST.OperatorExpression, types: List[MType]) -> MType:
        return MType.BOOL

    def _check_transpose_operator(self, node: AST.OperatorExpression, types: List[MType]) -> MType:
        exp_type = types[0]

        if len(exp_type.shape) != 2:
            self._error(node.line_span, 'Transposition can be performed only on 2d matrices')
            return MType.NONE

        return MType(exp_type.type, exp_type.shape[::-1])

    def _check_matrix_operators(self, node: AST.OperatorExpression, types: List[MType]) -> MType:

        # check if operator is applicable for that types
        if (MType(types[0].type), MType(types[1].type)) not in OPERATIONS[node.operator[1:]]:
            self._error(node.line_span,
                        f'Operator {node.operator} is not applicable for types {types[0]} and {types[1]}')
            return MType.NONE

        # check number of dimensions
        if len(types[0].shape) != len(types[1].shape):
            self._error(node.line_span,
                        f'Operator {node.operator} is not applicable for types with different number of dimensions ({len(types[0].shape)} and {len(types[1].shape)})')
            return MType.NONE

        # check size of dimensions if available and merge dimensions
        dim = []
        for i, (s1, s2) in enumerate(zip(iter(types[0].shape), iter(types[1].shape)), 1):
            dim.append(s1 or s2)
            if (s1 is not None) and (s2 is not None) and s1 != s2:
                self._error(node.line_span,
                            f'Operator {node.operator} is not applicable for types with different sizes of dimensions (dimension {i}: {s1} and {s2})')

        # get result type
        res_type = OPERATIONS[node.operator[1:]][(MType(types[0].type), MType(types[1].type))]
        return MType(res_type.type, tuple(dim))

    @check.register
    def _(self, node: AST.OperatorExpression) -> MType:

        # check arguments
        types = [self.check(e) for e in node.expressions]

        # check for normal operators
        if node.operator in OPERATIONS:
            return self._check_normal_operators(node, types)

        # check for equality operators
        if node.operator in ['==', '!=']:
            return self._check_equality_operators(node, types)

        # check for transpose operator
        if node.operator == "'":
            return self._check_transpose_operator(node, types)

        # check for element wise matrix operations
        if node.operator in ['.+', '.-', '.*', './']:
            return self._check_matrix_operators(node, types)

        # this should not happen
        self._error(node.line_span, f'Unexpected operator {node.operator}')
        return MType.NONE

    @check.register
    def _(self, node: AST.FunctionExpression) -> MType:
        arg_types = [self.check(a) for a in node.arguments]

        # function 'eye' accepts exactly two arguments
        if node.name == 'eye' and len(arg_types) != 2:
            self._error(node.line_span, f'Function eye expects exactly two arguments, while {len(arg_types)} were found')

        # check types
        for i, arg in enumerate(arg_types, 1):
            if arg != MType.INT:
                self._error(node.line_span, f'Function {node.name} expects integers as arguments, while argument number {i} is of type {arg}')

        return MType(float, (None, ) * len(node.arguments))

    # ==============================================
    #   VARIABLES
    # ==============================================
    @check.register
    def _(self, node: AST.Identifier) -> MType:

        # check if exists
        if node.name not in self.symbol_table:
            self._error(node.line_span, f'Variable {node.name} is not defined')
            return MType.NONE

        return self.symbol_table[node.name]

    @check.register
    def _(self, node: AST.Selector) -> MType:

        # check identifier and selector
        var_type = self.check(node.identifier)
        sel_type = self.check(node.selector)

        # check selector type
        if sel_type.type != int or len(sel_type.shape) != 1:
            self._error(node.line_span, 'Selector has to be a list of integers')
            return MType.NONE

        # check selector size if known at runtime
        if sel_type.shape[0] is not None and sel_type.shape[0] > len(var_type.shape):
            self._error(node.line_span, f'Selector has more arguments than variable has dimensions')
            return MType.NONE

        return MType(var_type.type, var_type.shape[sel_type.shape[0]:])

    # ==============================================
    #   STATEMENTS
    # ==============================================
    @check.register
    def _(self, node: AST.ProgramStatement) -> MType:
        with self.symbol_table.context_scope('program'):
            for statement in node.statements:
                self.check(statement)

        return MType.NONE

    @check.register
    def _(self, node: AST.AssignmentStatement) -> MType:

        # if its an assignment to identifier save to table
        if isinstance(node.variable, AST.Identifier):
            self.symbol_table[node.variable.name] = self.check(node.expression)

        # if its an assignment to selector check type compatibility
        if isinstance(node.variable, AST.Selector):
            var_type = self.check(node.variable)
            exp_type = self.check(node.expression)
            if var_type != exp_type:
                self._error(node.line_span, f'Cannot assign value of type {exp_type} to index of type {var_type}')

        return MType.NONE

    @check.register
    def _(self, node: AST.AssignmentWithOperatorStatement) -> MType:

        # get types
        types = (self.check(node.variable), self.check(node.expression))

        # check type compatibility
        if types not in OPERATIONS[node.operator[:-1]]:
            self._error(node.line_span, f'Operator {node.operator} is not applicable for types {types[0]} and {types[1]}')
            return MType.NONE

        # get type of result of operation
        res_type = OPERATIONS[node.operator[:-1]][types]

        # if variable is identifier we can just set its type
        if isinstance(node.variable, AST.Identifier):
            self.symbol_table[node.variable.name] = res_type

        # if variable is selector we have have to check type compatibility
        if isinstance(node.variable, AST.Selector):
            if types[0] != res_type:
                self._error(node.line_span, f'Cannot assign value of type {res_type} to index of type {types[0]}')

        return MType.NONE

    @check.register
    def _(self, node: AST.WhileStatement) -> MType:

        # condition
        if self.check(node.condition) != MType.BOOL:
            self._error(node.line_span, 'Condition in while statement has to evaluate to boolean')

        # statement
        with self.symbol_table.context_scope('loop'):
            self.check(node.statement)

        return MType.NONE

    @check.register
    def _(self, node: AST.ForStatement) -> MType:

        # identifier and range
        self.symbol_table[node.identifier.name] = self.check(node.range)

        # statement
        with self.symbol_table.context_scope('loop'):
            self.check(node.statement)

        return MType.NONE

    @check.register
    def _(self, node: AST.IfStatement) -> MType:

        # condition expression
        if self.check(node.condition) != MType.BOOL:
            self._error(node.line_span, 'Condition in if statement has to evaluate to boolean')

        # then statement
        with self.symbol_table.context_scope('then'):
            self.check(node.statement_then)

        # else statement
        if node.statement_else:
            with self.symbol_table.context_scope('else'):
                self.check(node.statement_else)

        return MType.NONE


class TypeCheckerError(CompilerError):
    def __init__(self, line_span: Tuple[int, int], msg: str) -> None:
        super().__init__('TypeChecker', line_span[0], msg)

        self.line_span = line_span
