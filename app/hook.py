import inspect
import struct
import os
from loguru import logger
from pymem.process import inject_dll, module_from_name
from signatures import (
    dialog_trigger,
#    cutscene_trigger,
    pyrun_simplestring,
    py_initialize_ex,
    quest_text_trigger,
    walkthrough_text,
#    cutscene_start,
    npc_indx_load
)
from memory import (
    dqx_mem,
    read_bytes,
    write_bytes,
    write_string,
    pattern_scan
)
from api_translate.dialog import translate_shellcode, load_evtx_shellcode
#from api_translate.cutscene import cutscene_shellcode
from api_translate.quest import quest_text_shellcode
from api_translate.walkthrough import walkthrough_shellcode
from hook_mgmt.hide_hooks import load_unload_hooks
from translate import determine_translation_service

def allocate_memory(size: int) -> int:
    '''
    Allocates a defined number of bytes into the target process.
    '''
    return PYM_PROCESS.allocate(size)

def pack_to_int(address: int) -> bytes:
    '''
    Packs the address into little endian and returns the appropriate bytes.
    '''
    return struct.pack('<i', address)

def unpack_to_int(address: int):
    '''
    Unpacks the address from little endian and returns the appropriate bytes.
    '''
    value = read_bytes(address, 4)
    unpacked_address = struct.unpack('<i', value)

    return unpacked_address

def calc_rel_addr(origin_address: int, destination_address: int) -> bytes:
    '''
    Calculates the difference between addresses to return the relative offset.
    '''

    # jmp forward
    if origin_address < destination_address:
        return bytes(pack_to_int(abs(origin_address - destination_address + 5)))

    # jmp backwards
    else:
        offset = -abs(origin_address - destination_address)
        unsigned_offset = offset + 2**32
        return unsigned_offset.to_bytes(4, 'little')

def get_stolen_bytes(jump_address: int, number_of_bytes_to_steal: int) -> bytes:
    '''
    Gets the original jump bytecode before it is written over. AKA "Stolen bytes"
    '''
    return read_bytes(jump_address, number_of_bytes_to_steal)

def get_hook_bytecode(hook_address: int):
    '''
    Returns a formatted jump address for your hook.
    '''
    return b'\xE9' + pack_to_int(hook_address)

def write_pre_hook_registers() -> dict:
    '''
    Captures memory registers prior to your hook code being executed.

    This writes the instructions to capture the registers. The actual
    values are being written to a different allocated address.

    If you're going to detour, you will want to jump to this address first.
    '''
    mov_insts = allocate_memory(50)   # allocate memory for memory instructions
    reg_values = allocate_memory(50)  # allocate memory for register values

    write_bytes(mov_insts, b'\xA3' + pack_to_int(reg_values) + b'\x90')       # mov [reg_values], eax then nop
    write_bytes(mov_insts + 6, b'\x89\x1D' + pack_to_int(reg_values + 4))     # mov [reg_values+6], ebx
    write_bytes(mov_insts + 12, b'\x89\x0D' + pack_to_int(reg_values + 8))    # mov [reg_values+12], ecx
    write_bytes(mov_insts + 18, b'\x89\x15' + pack_to_int(reg_values + 12))   # mov [reg_values+18], edx
    write_bytes(mov_insts + 24, b'\x89\x35' + pack_to_int(reg_values + 16))   # mov [reg_values+24], esi
    write_bytes(mov_insts + 30, b'\x89\x3D' + pack_to_int(reg_values + 20))   # mov [reg_values+30], edi
    write_bytes(mov_insts + 36, b'\x89\x2D' + pack_to_int(reg_values + 24))   # mov [reg_values+36], ebp
    write_bytes(mov_insts + 42, b'\x89\x25' + pack_to_int(reg_values + 28))   # mov [reg_values+42], esp

    addresses_dict = dict()
    addresses_dict['begin_mov_insts'] = mov_insts         # address where register backups occur
    addresses_dict['begin_hook_insts'] = mov_insts + 48   # address where to start hook instructions
    addresses_dict['begin_reg_values'] = reg_values       # address where to restore register backups
    addresses_dict['reg_eax'] = reg_values                # address that's in eax pre-hook
    addresses_dict['reg_ebx'] = reg_values + 4            # address that's in ebx pre-hook
    addresses_dict['reg_ecx'] = reg_values + 8            # address that's in ecx pre-hook
    addresses_dict['reg_edx'] = reg_values + 12           # address that's in edx pre-hook
    addresses_dict['reg_esi'] = reg_values + 16           # address that's in esi pre-hook
    addresses_dict['reg_edi'] = reg_values + 20           # address that's in edi pre-hook
    addresses_dict['reg_ebp'] = reg_values + 24           # address that's in ebp pre-hook
    addresses_dict['reg_esp'] = reg_values + 28           # address that's in esp pre-hook

    return addresses_dict

def write_post_hook_registers(pre_register_value_addr: int, hook_instr_end: int) -> dict:
    '''
    Reverts the current registers back to their previous values before the hook.

    Args:
        * pre_register_value_addr: Address where the pre-hook stored the original registers
        * hook_instr_end: End of custom hook code to write post hook mov's
    '''
    write_bytes(hook_instr_end, b'\xA1' + pack_to_int(pre_register_value_addr) + b'\x90')       # mov eax, [pre_register_value_addr] then nop
    write_bytes(hook_instr_end + 6, b'\x8B\x1D' + pack_to_int(pre_register_value_addr + 4))     # mov ebx, [pre_register_value_addr+6]
    write_bytes(hook_instr_end + 12, b'\x8B\x0D' + pack_to_int(pre_register_value_addr + 8))    # mov ecx, [pre_register_value_addr+12]
    write_bytes(hook_instr_end + 18, b'\x8B\x15' + pack_to_int(pre_register_value_addr + 12))   # mov edx, [pre_register_value_addr+18]
    write_bytes(hook_instr_end + 24, b'\x8B\x35' + pack_to_int(pre_register_value_addr + 16))   # mov esi, [pre_register_value_addr+24]
    write_bytes(hook_instr_end + 30, b'\x8B\x3D' + pack_to_int(pre_register_value_addr + 20))   # mov edi, [pre_register_value_addr+30]
    write_bytes(hook_instr_end + 36, b'\x8B\x2D' + pack_to_int(pre_register_value_addr + 24))   # mov ebp, [pre_register_value_addr+36]
    write_bytes(hook_instr_end + 42, b'\x8B\x25' + pack_to_int(pre_register_value_addr + 28))   # mov esp, [pre_register_value_addr+42]

    addresses_dict = dict()
    addresses_dict['end_mov_insts'] = hook_instr_end + 48  # address where register restore ends

    return addresses_dict

def convert_dict(hook_name: str, detour_address: int, shellcode_address: int, original_bytes: bytes, hook_bytes: bytes, begin_hook_addr: int, end_hook_addr: int) -> dict:
    '''
    Creates a dict to feed to hook manager.
    '''
    dictionary = dict()
    dictionary['hook_name'] = hook_name
    dictionary['detour_address'] = detour_address
    dictionary['shellcode_address'] = shellcode_address
    dictionary['original_bytes'] = original_bytes
    dictionary['hook_bytes'] = hook_bytes
    dictionary['hook_begin_addr'] = begin_hook_addr
    dictionary['hook_end_addr'] = end_hook_addr

    return dictionary

def inject_python_dll():
    '''
    Injects a Python dll.
    '''
    try:
        dll = os.getcwd() + '\python39.dll'
        if module_from_name(PYM_PROCESS.process_handle, 'python39.dll'):
            logger.debug('Python dll already injected. Skipping.')
            return False

        inject_dll(PYM_PROCESS.process_handle, bytes(dll, 'ascii'))
        if module_from_name(PYM_PROCESS.process_handle, 'python39.dll'):
            logger.debug('Python dll injected!')
            py_initialize_addr = pattern_scan(py_initialize_ex, module='python39.dll')
            write_bytes(py_initialize_addr, b'\x6A\x00')  # push 0 to initsigs
            return
        else:
            logger.error('Python dll failed to inject.')
            return False
    except:
        logger.error('Python dll failed to inject.')
        return False

def inject_bypass():
    '''
    Injects bypass dll.
    '''
    try:
        dll = os.getcwd() + '\DQXBypass.dll'
        if module_from_name(PYM_PROCESS.process_handle, 'DQXBypass.dll'):
            logger.debug('Bypass dll already injected. Skipping.')
            return False

        inject_dll(PYM_PROCESS.process_handle, bytes(dll, 'ascii'))
        if module_from_name(PYM_PROCESS.process_handle, 'DQXBypass.dll'):
            logger.debug('Bypass dll injected!')
            return
        else:
            logger.error('Bypass dll failed to inject.')
            return False
    except:
        logger.error('Bypass dll failed to inject.')
        return False

def inject_py_shellcode(shellcode: str):
    '''
    Injects shellcode into DQX.
    '''
    return PYM_PROCESS.inject_python_shellcode(shellcode)

def generic_detour(hook_name: str, pre_hook: dict, signature: bytes, num_bytes_to_steal: int, shellcode='', custom_bytecode=b'', initial_write=False) -> dict:
    '''
    Generic hook that should cover most hook needs.
    Returns a dict of hook_name, detour_address, shellcode_addr, original_bytes and hook_bytes.
    '''
    detour_address = pattern_scan(signature, module='DQXGame.exe')

    if shellcode:
        pyrun_simplestring_addr = pattern_scan(pyrun_simplestring, module='python39.dll')
        py_initialize_ex_addr = pattern_scan(py_initialize_ex, module='python39.dll')
        shellcode_addr = allocate_memory(len(shellcode))

        # write our shellcode
        write_string(shellcode_addr, shellcode)

        bytecode = (b'\xE8' + calc_rel_addr(pre_hook['begin_hook_insts'], py_initialize_ex_addr))  # call py_initialize_ex_addr
        bytecode += (b'\x68' + bytes(pack_to_int(shellcode_addr))) # push shellcode_addr
        bytecode += (b'\xE8' + calc_rel_addr(pre_hook['begin_hook_insts'] + len(bytecode), pyrun_simplestring_addr)) # push py_run_simple_string_addr

        # write our hook code
        write_bytes(pre_hook['begin_hook_insts'], bytecode)
    
    elif custom_bytecode:
        bytecode = custom_bytecode
        shellcode_addr = 0
        write_bytes(pre_hook['begin_hook_insts'], bytecode)

    # revert our registers to before the hooking took place
    post_hook = write_post_hook_registers(pre_hook['begin_reg_values'], pre_hook['begin_hook_insts'] + len(bytecode))

    # find function address and read bytes to steal
    stolen_bytecode = get_stolen_bytes(detour_address, num_bytes_to_steal)

    # write stolen bytes to end of our hook function
    write_bytes(post_hook['end_mov_insts'], stolen_bytecode)

    # jmp back to original function
    if num_bytes_to_steal > 5:
        count = num_bytes_to_steal - 5
    else:
        count = 0
    bytecode = (b'\xE9' + calc_rel_addr(post_hook['end_mov_insts'] + num_bytes_to_steal, detour_address + count))
    write_bytes(post_hook['end_mov_insts'] + num_bytes_to_steal, bytecode)

    # finally, write our mid function hook
    hook_bytecode = (b'\xE9' + calc_rel_addr(detour_address, pre_hook['begin_mov_insts']))
    if num_bytes_to_steal > 5:
        count = num_bytes_to_steal - 5
        for i in range(count):
            hook_bytecode += b'\x90'

    write_bytes(detour_address, hook_bytecode)

    logger.debug(f"{hook_name} address:      {hex(pre_hook['begin_mov_insts'])}")
    logger.debug(f"Shellcode address:        {hex(shellcode_addr)}")
    logger.debug(f"Detour address:           {hex(detour_address)}")

    return convert_dict(hook_name, detour_address, shellcode_addr, stolen_bytecode, hook_bytecode, pre_hook['begin_mov_insts'], post_hook['end_mov_insts'])

def translate_detour(debug: bool):
    '''
    Hooks the dialog window to translate text and write English instead.
    '''
    bytes_to_steal = 6
    
    pre_hook = write_pre_hook_registers()
    eax = pre_hook['reg_eax']
    ebx = pre_hook['reg_ebx']

    api_details = determine_translation_service()
    shellcode = translate_shellcode(
        eax,
        ebx,
        api_details['TranslateService'],
        api_details['TranslateKey'],
        api_details['IsPro'],
        api_details['EnableDialogLogging'],
        api_details['RegionCode'],
        debug)

    detour = generic_detour(
        inspect.currentframe().f_code.co_name,
        pre_hook,
        dialog_trigger,
        bytes_to_steal,
        shellcode=shellcode
    )

    return detour

#def cutscene_detour():
#    '''
#    Hooks the cutscene dialog to translate text and write English instead.
#    '''
#    bytes_to_steal = 5

#    pre_hook = write_pre_hook_registers()
#    edi = pre_hook['reg_edi']

#    shellcode = cutscene_shellcode(edi)

#    detour = generic_detour(
#        inspect.currentframe().f_code.co_name,
#        pre_hook,
#        cutscene_trigger,
#        bytes_to_steal,
#        shellcode=shellcode
#    )
#
#    return detour

def quest_text_detour(debug: bool):
    '''
    Hook the quest dialog window and translate to english.
    '''
    bytes_to_steal = 6

    pre_hook = write_pre_hook_registers()
    eax = pre_hook['reg_eax']

    api_details = determine_translation_service()
    shellcode = quest_text_shellcode(
        eax,
        api_details['TranslateService'],
        api_details['TranslateKey'],
        api_details['IsPro'],
        api_details['EnableDialogLogging'],
        api_details['RegionCode'],
        debug)

    detour = generic_detour(
        inspect.currentframe().f_code.co_name,
        pre_hook,
        quest_text_trigger,
        bytes_to_steal,
        shellcode=shellcode
    )

    return detour

def walkthrough_detour(debug: bool):
    '''
    Hook a specific instruction that allows us to change the walkthrough text.
    '''
    bytes_to_steal = 5

    pre_hook = write_pre_hook_registers()
    esi = pre_hook['reg_esi']

    api_details = determine_translation_service()
    shellcode = walkthrough_shellcode(
        esi,
        api_details['TranslateService'],
        api_details['TranslateKey'],
        api_details['IsPro'],
        api_details['EnableDialogLogging'],
        api_details['RegionCode'],
        debug)

    detour = generic_detour(
        inspect.currentframe().f_code.co_name,
        pre_hook,
        walkthrough_text,
        bytes_to_steal,
        shellcode=shellcode
    )

    return detour

#def cutscene_started_detour():
#   '''
#    Hook an instruction that is triggered when a cutscene is starting to transition the screen.
#    When this happens, we should immediately unhook all hooks as memory integrity checks start firing.
#    This detour will initially do nothing as unhook code will be passed when all hooks are passed
#    in activate_hooks.
#    '''
#    bytes_to_steal = 6
#    pre_hook = write_pre_hook_registers()
#
#    detour = generic_detour(
#        inspect.currentframe().f_code.co_name,
#        pre_hook,
#        cutscene_start,
#        bytes_to_steal,
#        custom_bytecode=b'\x90'
#    )
#
#    return detour

def load_indx_detour():
    '''
    Detours function where EVTX files are written to memory so we can write our own copy.
    Specifically, we detour when INDX is referenced as our hex_dict has these entries.
    '''
    bytes_to_steal = 6

    pre_hook = write_pre_hook_registers()
    ecx = pre_hook['reg_ecx']

    shellcode = load_evtx_shellcode(ecx)

    detour = generic_detour(
        inspect.currentframe().f_code.co_name,
        pre_hook,
        npc_indx_load,
        bytes_to_steal,
        shellcode=shellcode
    )

    return detour

def activate_hooks(debug: bool):
    '''
    Activates all hooks and kicks off hook manager.
    '''
    inject_python_dll()

    # activates all hooks. add any new hooks to this list
    hooks = []
    #hooks.append(walkthrough_detour(debug)) // leaving disabled until can figure out how to hide from checks
#    hooks.append(cutscene_started_detour())
    hooks.append(translate_detour(debug))
#    hooks.append(cutscene_detour())
    hooks.append(quest_text_detour(debug))
    hooks.append(load_indx_detour())

    # any hooks that need to perform unhooking should be defined here
    unhookers = ['cutscene_started_detour']

    # construct our asm to detach hooks
    unhook_bytecode = b''
    for hook in hooks:
        orig_address = hook['detour_address']
        orig_bytes = hook['original_bytes']
        for byte in orig_bytes:
            packed_address = struct.pack('<i', orig_address)
            unhook_bytecode += b'\xC6\x05'                # mov byte ptr
            unhook_bytecode += packed_address             # address to move byte to
            unhook_bytecode += byte.to_bytes(1, 'little') # byte to move
            orig_address += 1

    # these functions need to perform the unhooking, so overwrite their code here.
    for hook in hooks:
        if hook['hook_name'] in unhookers:
            stolen_byte_len = len(hook['original_bytes'])
            if stolen_byte_len > 5:
                count = stolen_byte_len - 5
            else:
                count = 0
            unhook_bytecode_with_jump = (
                unhook_bytecode + 
                b'\xE9' + calc_rel_addr(hook['hook_begin_addr'] + len(unhook_bytecode), hook['detour_address'] - stolen_byte_len + count)
            )
            write_bytes(hook['hook_begin_addr'], unhook_bytecode_with_jump)

    load_unload_hooks(hooks, debug)

PYM_PROCESS = dqx_mem()
