#
# A class which represents the Program Counter and decodes instructions
# to be executed by the ZPU.  Implements section 4 of Z-code specification.
#
# For the license of this file, please consult the LICENSE file in the
# root directory of this distribution.
#

from bitfield import BitField
from zmemory import ZMemory

class ZOperationError(Exception):
  "General exception for ZOperation class"
  pass

# Constants defining the known instruction types. These types are
# related to the number of operands the opcode has: for each operand
# count, there is a separate opcode table, and the actual opcode
# number is an index into that table.
OPCODE_0OP = 0
OPCODE_1OP = 1
OPCODE_2OP = 2
OPCODE_VAR = 3
OPCODE_EXT = 4

# Mapping of those constants to strings describing the opcode
# classes. Used for pretty-printing only.
OPCODE_STRINGS = {
  OPCODE_0OP: '0OP',
  OPCODE_1OP: '1OP',
  OPCODE_2OP: '2OP',
  OPCODE_VAR: 'VAR',
  OPCODE_EXT: 'EXT',
  }

# Constants defining the possible operand types.
LARGE_CONSTANT = 0x0
SMALL_CONSTANT = 0x1
VARIABLE = 0x2
ABSENT = 0x3

class ZOpDecoder(object):
  def __init__(self, zmem, zstack):
    ""
    self._memory = zmem
    self._stack = zstack
    self._parse_map = {}
    self.program_counter = self._memory.read_word(0x6)

  def _get_pc(self):
    byte = self._memory[self.program_counter]
    self.program_counter += 1
    return byte

  def get_next_instruction(self):
    """Decode the opcode & operands currently pointed to by the
    program counter, and appropriately increment the program counter
    afterwards. A decoded operation is returned to the caller in the form:

       [opcode-class, opcode-number, [operand, operand, operand, ...]]

    If the opcode has no operands, the operand list is present but empty."""

    opcode = self._get_pc()

    # Determine the opcode type, and hand off further parsing.
    if self._memory.version == 5 and opcode == 0xBE:
      # Extended opcode
      return self._parse_opcode_extended()

    opcode = BitField(opcode)
    if opcode[7] == 0:
      # Long opcode
      return self._parse_opcode_long(opcode)
    elif opcode[6] == 0:
      # Short opcode
      return self._parse_opcode_short(opcode)
    else:
      # Variable opcode
      return self._parse_opcode_variable(opcode)

  def _parse_opcode_long(self, opcode):
    """Parse an opcode of the long form."""
    # Long opcodes are always 2OP. The types of the two operands are
    # encoded in bits 5 and 6 of the opcode.
    LONG_OPERAND_TYPES = [SMALL_CONSTANT, VARIABLE]
    operands = [self._parse_operand(LONG_OPERAND_TYPES[opcode[6]]),
                self._parse_operand(LONG_OPERAND_TYPES[opcode[5]])]
    return (OPCODE_2OP, opcode[0:5], operands)

  def _parse_opcode_short(self, opcode):
    """Parse an opcode of the short form."""
    # Short opcodes can have either 1 operand, or no operand.
    operand_type = opcode[4:6]
    operand = self._parse_operand(operand_type)
    if operand is None: # 0OP variant
      return (OPCODE_0OP, opcode[0:4], [])
    else:
      return (OPCODE_1OP, opcode[0:4], [operand])

  def _parse_opcode_variable(self, opcode):
    """Parse an opcode of the variable form."""
    if opcode[5]:
      opcode_type = OPCODE_VAR
    else:
      opcode_type = OPCODE_2OP

    opcode_num = opcode[0:5]

    # Parse the types byte to retrieve the operands.
    operands = self._parse_operands_byte()

    # Special case: opcodes 12 and 26 have a second operands byte.
    if opcode_num == 0xC or opcode_num == 0x1A:
      operands += self._parse_operands_byte()

    return (opcode_type, opcode_num, operands)

  def _parse_operand(self, operand_type):
    """Read and return an operand of the given type.

    This assumes that the operand is in memory, at the address pointed
    by the Program Counter."""
    assert operand_type <= 0x3

    if operand_type == LARGE_CONSTANT:
      operand = self._memory.read_word(self.program_counter)
      self.program_counter += 2
    elif operand_type == SMALL_CONSTANT:
      operand = self._get_pc()
    elif operand_type == VARIABLE:
      variable_number = self._get_pc()
      if variable_number == 0:
        operand = self._stack.pop_stack() # TODO: make sure this is right.
      elif variable_number < 16:
        operand = self._stack.get_local_variable(variable_number - 1)
      else:
        operand = self._memory.read_global(variable_number)
    elif operand_type == ABSENT:
      operand = None

    return operand

  def _parse_operands_byte(self):
    """Parse operands given by the operand byte and return a list of
    values.
    """
    operand_byte = BitField(self._get_pc())
    operands = []
    for operand_type in [operand_byte[6:8], operand_byte[4:6],
                         operand_byte[2:4], operand_byte[0:2]]:
      operand = self._parse_operand(operand_type)
      if operand is None:
        break
      operands.append(operand)

    return operands


  # Public funcs that the ZPU may also need to call, depending on the
  # opcode being executed:

  def get_zstring(self):
    """For string opcodes, return the zstring pointed to by the PC.
    Increment PC just past the text.  (The caller is responsible for
    converting the bytes into ascii.)"""

    start_addr = self.program_counter
    bf = BitField(0)

    while True:
      bf.__init__(self._memory.read_word(self.program_counter))
      program_counter += 2
      if bf[15] == 1:
        break

    return self._memory[start:self.program_counter]


  def get_store_address(self):
    """For store opcodes, read byte pointed to by PC and return the
    variable number in which the operation result should be stored.
    Increment the PC as necessary."""
    return self._get_pc()


  def get_branch_offset(self):
    """For branching opcodes, examine address pointed to by PC, and
    return two values: first, either True or False (indicating whether
    to branch if true or branch if false), and second, the address to
    jump to.  Increment the PC as necessary."""

    bf = BitField(self._memory.read_word(self.program_counter))
    if bf[14] == 1:
      self.program_counter += 1
      print "*** Branch offset (0-63) : %d" % bf[8:14]
      return bf[15], bf[8:14]
    else:
      self.program_counter += 2

      # We need to do a little magic here. The branch offset is
      # written as a signed 14-bit number, with signed meaning '-n' is
      # written as '65536-n'. Or in this case, as we have 14 bits,
      # '16384-n'.
      #
      # So, if the MSB (ie. bit 13) is set, we have a negative
      # number. We take the value, and substract 16384 to get the
      # actual offset as a negative integer.
      #
      # If the MSB is not set, we just extract the value and return it.
      #
      # Can you spell "Weird" ?
      if bf[13] == 1:
        print "*** Branch offset (14-bit) : %d" % bf[8:14]
        return bf[15], bf[0:14] - 16384
      else:
        print "*** Branch offset (0-63) : %d" % bf[8:14]
        return bf[15], bf[0:14]
