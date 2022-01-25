import sys
import os
from json import dumps

def translate_shellcode(
    eax_address: int,
    ebx_address: int,
    api_service: str,
    api_key: str,
    api_pro: str,
    api_logging: str,
    api_region: str,
    debug: bool) -> str:
    '''
    Returns shellcode for the translate function hook.
    eax_address: Where text can be modified to be fed to the screen
    ebx_address: NPC name
    '''
    local_paths = dumps(sys.path).replace('\\', '\\\\')
    working_dir = dumps(os.getcwd()).replace('\\', '\\\\')

    shellcode = fr"""
import sys
import os
from traceback import format_exc
from os import chdir

local_paths = {local_paths}
working_dir = {working_dir}
debug = {debug}
api_logging = {api_logging}

sys.path = local_paths
chdir(working_dir)

try:
    from datetime import datetime
    import logging
    from hook import unpack_to_int
    from clarity import write_adhoc_entry
    from memory import (
        read_bytes,
        write_bytes,
        read_string,
        find_first_match,
        scan_backwards)
    from translate import (
        sanitized_dialog_translate,
        sqlite_read,
        sqlite_write,
        detect_lang
    )
    from errors import AddressOutOfRange
    from signatures import index_pattern, foot_pattern

    def setup_logger(name, log_file, level=logging.INFO):
        formatter = logging.Formatter('%(asctime)s %(message)s')
        handler = logging.FileHandler(log_file, encoding='utf-8')
        handler.setFormatter(formatter)

        logger = logging.getLogger(name)
        if (logger.hasHandlers()):
            logger.handlers.clear()

        logger.setLevel(level)
        logger.addHandler(handler)

        return logger

    logger = setup_logger('out', 'out.log')
    if debug:
        logger.setLevel(logging.DEBUG)
    game_text_logger = setup_logger('gametext', 'game_text.log')

    # get address values where text can be identified
    npc_address = unpack_to_int({ebx_address})[0]
    ja_address = unpack_to_int({eax_address})[0]

    ja_text = read_string(ja_address)

    if api_logging:
        game_text_logger.info(ja_text)

    if detect_lang(ja_text):
        if find_first_match(ja_address, foot_pattern) != False:
            logger.debug('Adhoc address found at >> ' + str(hex(ja_address)) + ' <<')
            adhoc_address = scan_backwards(ja_address, index_pattern)
            if adhoc_address:
                if read_bytes(adhoc_address - 2, 1) != b'\x69':
                    logger.debug('Found INDX section :: Checking if we have this file')
                    adhoc_bytes = read_bytes(adhoc_address, 64)
                    if adhoc_bytes:
                        logger.debug('Adhoc bytes read.')
                        adhoc_write = write_adhoc_entry(adhoc_address, str(adhoc_bytes.hex()))
                        if adhoc_write['success']:
                            logger.debug('Wrote adhoc file.')
                        elif adhoc_write['file'] is not None:
                            logger.debug('New adhoc file. Writing to new_adhoc_dumps.')
                        elif adhoc_write['file'] is None:
                            logger.debug('This file already exists in new_adhoc_dumps. Needs merge into github.')
                        write_bytes(adhoc_address - 2, b'\x69')  # leave our mark to let us know we wrote this. nice.
            else:
                logger.debug('Did not find INDX section. Shit. You should probably report this.')
        else:
            logger.debug('Dynamic address found at >> ' + str(hex(ja_address)) + ' << :: Checking if translation is needed')
            try:
                npc = read_string(npc_address)
            except:
                npc = ''

            logger.debug(ja_text)
            result = sqlite_read(ja_text, '{api_region}', 'dialog')

            if result is not None:
                logger.debug('found database entry. no translation needed')
                write_bytes(ja_address, result.encode() + b'\x00')
            else:
                logger.debug('translation needed. sending to {api_service}')
                translated_text = sanitized_dialog_translate('{api_service}', '{api_pro}', ja_text, '{api_key}', '{api_region}')
                logger.debug(translated_text)
                sqlite_write(ja_text, 'dialog', translated_text, '{api_region}', npc_name=npc)
                logger.debug('database record inserted.')
                write_bytes(ja_address, translated_text.encode() + b'\x00')
    else:
        logger.debug('English detected. Doing nothing.')
except AddressOutOfRange:
    pass
except:
    with open('out.log', 'a+') as f:
        f.write(format_exc())
    """

    return str(shellcode)

def load_evtx_shellcode(
    edx_address: int
) -> str:
    '''
    Returns shellcode for the evtx load hook.
    edx_address: Address where INDX starts
    '''
    local_paths = dumps(sys.path).replace('\\', '\\\\')
    working_dir = dumps(os.getcwd()).replace('\\', '\\\\')

    shellcode = fr"""
import sys
import os
from traceback import format_exc
from os import chdir

local_paths = {local_paths}
working_dir = {working_dir}
sys.path = local_paths
chdir(working_dir)

try:
    import logging
    from hook import unpack_to_int
    from clarity import write_adhoc_entry, query_csv
    from memory import read_bytes
    from signatures import index_pattern

    def setup_logger(name, log_file, level=logging.INFO):
        formatter = logging.Formatter('%(asctime)s %(message)s')
        handler = logging.FileHandler(log_file, encoding='utf-8')
        handler.setFormatter(formatter)

        logger = logging.getLogger(name)
        if (logger.hasHandlers()):
            logger.handlers.clear()

        logger.setLevel(level)
        logger.addHandler(handler)

        return logger

    logger = setup_logger('out', 'out.log')

    # get address values where text can be identified
    indx_address = unpack_to_int({edx_address})[0]
    logger.debug('adhoc file encountered :: checking if we have this file')
    adhoc_bytes = read_bytes(indx_address, 64)
    file = query_csv(adhoc_bytes)
    if file:
        adhoc_write = write_adhoc_entry(indx_address, str(adhoc_bytes.hex()))
        if adhoc_write['success']:
            logger.debug('Wrote adhoc file.')
        elif adhoc_write['file'] is not None:
            logger.debug('New cutscene adhoc file. Will write to new_adhoc_dumps if it does not already exist.')
        elif adhoc_write['file'] is None:
            logger.debug('This file already exists in new_adhoc_dumps. Needs merge into github.')
        write_bytes(adhoc_address - 2, b'\x69')  # write our state byte so we know we already wrote this. nice.
    else:
        logger.debug('We already wrote this cutscene file. Ignoring.')
except:
    with open('out.log', 'a+') as f:
        f.write(format_exc())
    """

    return str(shellcode)