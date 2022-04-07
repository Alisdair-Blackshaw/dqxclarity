# -*- coding: utf-8 -*-
from pathlib import Path
import csv
import json
import os
import re
import shutil
import sys
import time
import zipfile
import random
from alive_progress import alive_bar
import pykakasi
from loguru import logger
import logging

logging.basicConfig(
    format='%(message)s'
    )

import requests
from errors import messageBoxFatalError
import logging
from translate import (
    sqlite_read,
    sqlite_write,
    detect_lang,
    determine_translation_service,
    sanitized_dialog_translate
)
from memory import (
    read_bytes,
    read_string,
    write_string,
    write_bytes,
    pattern_scan,
    get_start_of_game_text,
    find_first_match
)
from signatures import (
    index_pattern,
    foot_pattern,
    npc_monster_byte_pattern,
    menu_ai_name_byte_pattern,
    player_name_byte_pattern,
  # comm_name_byte_pattern,
    walkthrough_pattern
)

def generate_hex(file):
    '''Parses a nested json file to convert strings to hex.'''
    en_hex_to_write = ''
    data = read_json_file(file)
    for item in data:
        key, value = list(data[item].items())[0]
        if re.search('^clarity_nt_char', key):
            en = '00'
        elif re.search('^clarity_ms_space', key):
            en = '00e38080'
        else:
            ja = key.encode('utf-8').hex() + '00'
            ja_raw = key
            ja_len = len(ja)
            if value:
                en = value.encode('utf-8').hex() + '00'
                en_raw = value
                en_len = len(en)
            else:
                en = ja
                en_len = ja_len
            if en_len > ja_len:
                logger.error('\n')
                logger.error('String too long. Please fix and try again.')
                logger.error(f'File: {file}.json')
                logger.error(f'JA string: {ja_raw} (byte length: {ja_len})')
                logger.error(f'EN string: {en_raw} (byte length: {en_len})')

            ja = ja.replace('7c', '0a')
            ja = ja.replace('5c74', '09')
            en = en.replace('7c', '0a')
            en = en.replace('5c74', '09')
            if ja_len != en_len:
                while True:
                    en += '00'
                    new_len = len(en)
                    if (ja_len - new_len) == 0:
                        break
        en_hex_to_write += en


    return en_hex_to_write

def get_latest_from_weblate():
    '''
    Downloads the latest zip file from the weblate branch and
    extracts the json files into the appropriate folder.
    '''
    # clean up previous attempts
    try:
        delete_file('weblate.zip')
        delete_folder('temp')
    except:
        pass

    # download zip from github
    try:
        url = 'https://github.com/xshobux/dqxclarity/archive/refs/heads/weblate.zip'
        r = requests.get(url, timeout=15)
    except:
        messageBoxFatalError('Timeout', 'Timed out trying to reach github.com. Relaunch Clarity without "Pull latest files from weblate" and try again.')

    # write request to file
    with open('weblate.zip', 'wb') as weblate_zip:
        weblate_zip.write(r.content)

    # unzip
    with zipfile.ZipFile('weblate.zip', 'r') as zipObj:
        zipObj.extractall('temp')
        delete_file('weblate.zip')

    # make sure json folder exists
    try:
        os.makedirs('json/_lang/en')
    except:
        pass

    # move json files
    json_path = 'temp/dqxclarity-weblate/json/_lang/en'
    json_files = os.listdir(json_path)
    for file in json_files:
        full_file_name = os.path.join(json_path, file)
        if os.path.isfile(full_file_name):
            shutil.copy(full_file_name, 'json/_lang/en')

    # copy hex dict
    hex_path = 'temp/dqxclarity-weblate/app/hex_dict.csv'
    if os.path.isfile(hex_path):
        shutil.copy(hex_path, os.getcwd())

    # cleanup
    delete_file('weblate.zip')
    delete_folder('temp')

    logger.info('Now up to date!')

def translate():
    '''Executes the translation process.'''
    index_list = pattern_scan(pattern=index_pattern, return_multiple=True)
    list_length = len(index_list)

    with alive_bar(list_length, title='Translating..', theme='musical', length=20) as bar:
        for index_address in index_list:
            bar()
            hex_result = split_hex_into_spaces(str(read_bytes(index_address, 64).hex()))
            csv_result = query_csv(hex_result)
            if csv_result:
                file = csv_result['file']
                hex_to_write = bytes.fromhex(generate_hex(file))
                text_address = get_start_of_game_text(index_address)
                if text_address:
                    try:
                        # this just tests that we can decode what we should be writing
                        game_hex = read_bytes(text_address, len(hex_to_write)).hex()
                        decoded_bytes = bytes.fromhex(game_hex).decode('utf-8')
                        if not find_first_match(text_address, foot_pattern):
                            continue
                    except:
                        continue

                    write_bytes(text_address, hex_to_write)

    logging.warning('')

def write_adhoc_entry(start_addr: int, hex_str: str) -> dict:
    '''
    Checks the stored json files for a matching adhoc file. If found,
    converts the json into bytes and writes bytes at the appropriate
    address.
    '''
    results = dict()
    hex_result = split_hex_into_spaces(hex_str)
    csv_result = query_csv(hex_result)
    if csv_result:
        file = csv_result['file']
        if file:
            hex_to_write = bytes.fromhex(generate_hex(file))
            index_address = find_first_match(start_addr, index_pattern)
            if index_address:
                text_address = get_start_of_game_text(index_address)
                if text_address:
                    write_bytes(text_address, hex_to_write)
                    results['success'] = True
                    results['file'] = file
                    return results
    else:
        results['success'] = False
        filename = str(random.randint(1, 1000000000))
        Path('new_adhoc_dumps/en').mkdir(parents=True, exist_ok=True)
        Path('new_adhoc_dumps/ja').mkdir(parents=True, exist_ok=True)

        csv_path = 'new_adhoc_dumps/new_hex_dict.csv'
        new_csv = Path(csv_path)
        if new_csv.is_file():
            csv_result = query_csv(hex_result, csv_path)
            if csv_result:  # if we have an entry, don't make another one
                results['file'] = None
                return results
        else:
            write_file('new_adhoc_dumps', 'new_hex_dict.csv', 'a', 'file,hex_string\n')

        # get number of bytes to read from start
        begin_address = get_start_of_game_text(start_addr)  # make sure we start on the first byte of the first letter
        end_address = find_first_match(begin_address, foot_pattern)
        bytes_to_read = end_address - begin_address

        # dump game file
        game_file = dump_game_file(begin_address, bytes_to_read)
        ja_data = game_file['ja']
        en_data = game_file['en']
        write_file('new_adhoc_dumps', 'new_hex_dict.csv', 'a', f'{filename},{hex_result}\n')
        write_file('new_adhoc_dumps/ja', f'{filename}.json', 'w', ja_data)
        write_file('new_adhoc_dumps/en', f'{filename}.json', 'w', en_data)
        results['file'] = filename
        return results

def scan_for_adhoc_files():
    '''
    Scans for specific adhoc files that have yet to have a hook written for them.
    '''
    cutscenes = False
    
    logger.info('Starting adhoc file scanning.')
    
    with open('defaults.pref') as f:
        if 's' in f.read():
            cutscenes = True
            logger.info('Cutscene translations enabled.')

    while True:
        try:
            index_list = pattern_scan(pattern=index_pattern, return_multiple=True)

            for index_address in index_list:
                if read_bytes(index_address - 2, 1) != b'\x69':
                    hex_result = split_hex_into_spaces(str(read_bytes(index_address, 64).hex()))
                    csv_result = query_csv(hex_result)
                    if csv_result:
                        file = csv_result['file']
                        if 'adhoc_wd_' in file:
                            hex_to_write = bytes.fromhex(generate_hex(file))
                            text_address = get_start_of_game_text(index_address)
                            if text_address:
                                try:
                                    # this just tests that we can decode what we should be writing
                                    foot_address = find_first_match(text_address, foot_pattern)
                                    game_hex = read_bytes(text_address, foot_address - text_address)
                                    game_hex.decode('utf-8')
                                except:
                                    continue

                                # with the match we found, make sure the INDX is still here before we write
                                if split_hex_into_spaces(str(read_bytes(index_address, 64).hex())) == hex_result:
                                    write_bytes(text_address, hex_to_write)
                                    write_bytes(index_address - 2, b'\x69')  # our mark that we wrote here so we don't write again. nice.
                                    logger.debug(f'Wrote {file} @ {hex(index_address)}')
                        elif ('adhoc_cs_' in file) and (cutscenes == True):
                            hex_to_write = bytes.fromhex(generate_hex(file))
                            text_address = get_start_of_game_text(index_address)
                            if text_address:
                                try:
                                    # this just tests that we can decode what we should be writing
                                    foot_address = find_first_match(text_address, foot_pattern)
                                    game_hex = read_bytes(text_address, foot_address - text_address)
                                    game_hex.decode('utf-8')
                                except:
                                    continue

                                # with the match we found, make sure the INDX is still here before we write
                                if split_hex_into_spaces(str(read_bytes(index_address, 64).hex())) == hex_result:
                                    write_bytes(text_address, hex_to_write)
                                    write_bytes(index_address - 2, b'\x69')  # our mark that we wrote here so we don't write again. nice.
                                    logger.debug(f'Wrote {file} @ {hex(index_address)}')                                                                        
            else:
                time.sleep(.001)
                continue
        except:
            logger.warning('Cannot find DQX process. Must have closed? Exiting.')
            sys.exit()

def scan_for_overworld_names():
    '''
    Continuously scans the DQXGame process for known addresses
    that are related to a specific pattern to translate player,
    NPC, and monster names.
    '''
    player_names = False
    npc_names = False
    with open('defaults.pref') as f:
        if 'p' in f.read():
            player_names = True
            logger.info('Player names enabled.')
    with open('defaults.pref') as f:
        if 'n' in f.read():
            npc_names = True
            logger.info('NPC names enabled.')

    if player_names:
        kks = pykakasi.kakasi()
    if npc_names:
        npc_data = read_json_file('json/_lang/en/npc_names.json')
        monster_data = read_json_file('json/_lang/en/monsters.json')
            
    while True:
        # Communication window name scanning
        # if player_names:
            # try:
                # comm_list = pattern_scan(pattern=comm_name_byte_pattern, return_multiple=True)
                # if comm_list == []:                
                    # for address in comm_list:
                        # comm_name_address = address + 9
                        # try:
                            # ja_comm_name = read_string(comm_name_address)
                        # except UnicodeDecodeError:
                            # continue
                
                        # romaji_name = kks.convert(ja_comm_name)[0]['hepburn'].capitalize()
                        # if ja_comm_name == "サーバー０１":
                            # romaji_name = "Server 01"
                        # if ja_comm_name == "サーバー０２":
                            # romaji_name = "Server 02"
                        # if ja_comm_name == "サーバー０３":
                            # romaji_name = "Server 03"
                        # if ja_comm_name == "サーバー０４":
                            # romaji_name = "Server 04"
                        # if ja_comm_name == "サーバー０５":
                            # romaji_name = "Server 05"
                        # if ja_comm_name == "サーバー０６":
                            # romaji_name = "Server 06"
                        # if ja_comm_name == "サーバー０７":
                            # romaji_name = "Server 07"
                        # if ja_comm_name == "サーバー０８":
                            # romaji_name = "Server 08"
                        # if ja_comm_name == "サーバー０９":
                            # romaji_name = "Server 09"
                        # if ja_comm_name == "サーバー１０":
                            # romaji_name = "Server 10"
                        # if ja_comm_name == "サーバー１１":
                            # romaji_name = "Server 11"
                        # if ja_comm_name == "サーバー１２":
                            # romaji_name = "Server 12"
                        # if ja_comm_name == "サーバー１３":
                            # romaji_name = "Server 13"
                        # if ja_comm_name == "サーバー１４":
                            # romaji_name = "Server 14"
                        # if ja_comm_name == "サーバー１５":
                            # romaji_name = "Server 15"
                        # if ja_comm_name == "サーバー１６":
                            # romaji_name = "Server 16"
                        # if ja_comm_name == "サーバー１７":
                            # romaji_name = "Server 17"
                        # if ja_comm_name == "サーバー１８":
                            # romaji_name = "Server 18"
                        # if ja_comm_name == "サーバー１９":
                            # romaji_name = "Server 19"
                        # if ja_comm_name == "サーバー２０":
                            # romaji_name = "Server 20"
                        # if ja_comm_name == "サーバー２１":
                            # romaji_name = "Server 21"
                        # if ja_comm_name == "サーバー２２":
                            # romaji_name = "Server 22"
                        # if ja_comm_name == "サーバー２３":
                            # romaji_name = "Server 23"
                        # if ja_comm_name == "サーバー２４":
                            # romaji_name = "Server 24"
                        # if ja_comm_name == "サーバー２５":
                            # romaji_name = "Server 25"
                        # if ja_comm_name == "サーバー２６":
                            # romaji_name = "Server 26"
                        # if ja_comm_name == "サーバー２７":
                            # romaji_name = "Server 27"
                        # if ja_comm_name == "サーバー２８":
                            # romaji_name = "Server 28"
                        # if ja_comm_name == "サーバー２９":
                            # romaji_name = "Server 29"
                        # if ja_comm_name == "サーバー３０":
                            # romaji_name = "Server 30"
                        # if ja_comm_name == "サーバー３１":
                            # romaji_name = "Server 31"
                        # if ja_comm_name == "サーバー３２":
                            # romaji_name = "Server 32"
                        # if ja_comm_name == "サーバー３３":
                            # romaji_name = "Server 33"
                        # if ja_comm_name == "プレイエリア":
                            # romaji_name = "Play Area"
                        # if ja_comm_name == "カジノ":
                            # romaji_name = "Casino"
                        # write_bytes(comm_name_address, romaji_name.encode('utf-8') + b'\x00')                       
            # except TypeError:
                # logger.warning('Cannot find DQX process. Must have closed? Exiting.')
                # sys.exit()    

        # Player name scanning
        if player_names:
            try:
                player_list = pattern_scan(pattern=player_name_byte_pattern, return_multiple=True)
                if player_list != []:
                    for address in player_list:
                        player_name_address = address + 17
                        try:
                            ja_player_name = read_string(player_name_address)
                        except UnicodeDecodeError:
                            continue

                        romaji_name = kks.convert(ja_player_name)[0]['hepburn'].capitalize()
                        write_bytes(player_name_address, b'\x04' + romaji_name.encode('utf-8') + b'\x00')
            except TypeError:
                logger.warning('Cannot find DQX process. Must have closed? Exiting.')
                sys.exit()

        # NPC name scanning
        if npc_names:
            try:
                index_list = pattern_scan(pattern=npc_monster_byte_pattern, return_multiple=True)

                if index_list != []:
                    for address in index_list:
                        if read_bytes(address, 2) == b'\xF0\xA1':  # monsters
                            data = monster_data
                            name_addr = address + 12  # jump to name
                            end_addr = address + 12
                        elif read_bytes(address, 2) == b'\xA4\xB3':  # npcs
                            data = npc_data
                            name_addr = address + 12  # jump to name
                            end_addr = address + 12
                        elif read_bytes(address, 2) == b'\x58\xA4':  # AI
                            data = 'AI_NAME'
                            name_addr = address + 12  # jump to name
                            end_addr = address + 12
                        else:
                            continue

                        name_hex = bytearray()
                        result = ''
                        while result != b'\x00':
                            result = read_bytes(end_addr, 1)
                            end_addr = end_addr + 1
                            if result == b'\x00':
                                end_addr = end_addr - 1   # Remove the last 00

                            name_hex += result

                        name_hex = name_hex.rstrip(b'\x00')
                        try:
                            name = name_hex.decode('utf-8')
                        except UnicodeDecodeError:
                            continue
                
                        if data == "AI_NAME":
                            romaji_name = kks.convert(name)[0]['hepburn'].capitalize()
                            write_bytes(name_addr, b'\x04' + romaji_name.encode('utf-8') + b'\x00')
                        else:
                            for item in data:
                                key, value = list(data[item].items())[0]
                                if re.search(f'^{name}+$', key):
                                    if value:
                                        write_bytes(name_addr, str.encode(value) + b'\x00')
            except TypeError:
                logger.warning('Cannot find DQX process. Must have closed? Exiting.')
                sys.exit()

        time.sleep(.01)

def scan_for_menu_ai_names():
    '''
    Scans for the walkthrough address and translates when found, then translates menu AI names.
    '''
    kks = pykakasi.kakasi()
    ai_addresses_found = False
    
    while True:
        # Menu AI name scanning
        try:
            if not ai_addresses_found:
                if ai_list := pattern_scan(pattern=menu_ai_name_byte_pattern, return_multiple=True):
                    ai_addresses_found = True
            if ai_addresses_found:
                for address in ai_list:
                    ai_name_address = address + 56
                    try:
                        if ja_ai_name := read_string(ai_name_address):
                            romaji_ai_name = kks.convert(ja_ai_name)[0]['hepburn'].capitalize()
                            write_bytes(ai_name_address, romaji_ai_name.encode('utf-8') + b'\x00')
                        else:
                            continue
                    except UnicodeDecodeError:
                        continue
        except TypeError:
            logger.warning('Cannot find DQX process. Must have closed? Exiting.')
            sys.exit()

        time.sleep(.5)

def scan_for_walkthrough():
    '''
    Scans for the walkthrough address and translates when found.
    '''
    api_details = determine_translation_service()
    logger.info('Starting walkthrough scanning.')
    
    while True:
        try:
            if address := pattern_scan(pattern=walkthrough_pattern):
                prev_text = ''
                while True:
                        if text := read_string(address + 16):
                            if text != prev_text:
                                prev_text = text
                                if detect_lang(text):
                                    result = sqlite_read(text, 'en', 'walkthrough')
                                    if result:
                                        write_string(address + 16, result)
                                    else:
                                        translated_text = sanitized_dialog_translate(
                                            api_details['TranslateService'],
                                            api_details['IsPro'], 
                                            text,
                                            api_details['TranslateKey'],
                                            api_details['RegionCode'],
                                            text_width=31,
                                            max_lines=3
                                        )
                                        sqlite_write(
                                            text,
                                            'walkthrough',
                                            translated_text,
                                            api_details['RegionCode']
                                        )
                                        write_bytes(
                                            address + 16,
                                            translated_text.encode() + b'\x00'
                                        )
                            else:
                                time.sleep(.5)
            else:
                time.sleep(.5)
        except:
            logger.warning('Cannot find DQX process. Must have closed? Exiting.')
            sys.exit()

def dump_game_file(start_addr: int, num_bytes_to_read: int):
    '''
    Dumps a game file given its start and end address. Formats into a json
    friendly file to be used by clarity for both ja and en.

    start_addr: Where to start our read operation to dump (should start at TEXT)
    num_bytes_to_read: How many bytes should we should dump from the start_addr
    '''
    game_data = read_bytes(start_addr, num_bytes_to_read).hex().strip('00')
    if len(game_data) % 2 != 0:
        game_data = game_data + '0'

    game_data = bytes.fromhex(game_data).decode('utf-8')
    game_data = game_data.replace('\x0a', '\x7c')
    game_data = game_data.replace('\x00', '\x0a')
    game_data = game_data.replace('\x09', '\x5c\x74')

    jsondata_ja = {}
    jsondata_en = {}
    number = 1

    for line in game_data.split('\n'):
        json_data_ja = __format_to_json(jsondata_ja, line, 'ja', number)
        json_data_en = __format_to_json(jsondata_en, line, 'en', number)
        number += 1

    json_data_ja = json.dumps(
        jsondata_ja,
        indent=2,
        sort_keys=False,
        ensure_ascii=False
    )
    json_data_en = json.dumps(
        jsondata_en,
        indent=2,
        sort_keys=False,
        ensure_ascii=False
    )

    dic = dict()
    dic['ja'] = json_data_ja
    dic['en'] = json_data_en

    return dic

def check_for_updates():
    '''
    Checks github for updates.
    '''
    url = 'https://raw.githubusercontent.com/xshobux/dqxclarity/weblate/version.update'
    verfile = open('version.update','r')
    curVer = verfile.read()
    verfile.close()

    try:
        github_request = requests.get(url)
    except requests.exceptions.RequestException as e:
        logging.warning(f'Failed to check latest version. Running anyways.\nMessage: {e}')
        return

    if github_request.text != curVer:
        logging.warning('\nAn update is available at https://github.com/xshobux/dqxclarity/releases\n')
    else:
        logging.warning('\nUp to date!\n')
  
    return

def query_csv(hex_pattern, hex_dict='hex_dict.csv') -> dict:
    with open(hex_dict) as file:
        reader = csv.DictReader(file)
        return_dict = dict()
        for row in reader:
            if row['hex_string'] == hex_pattern:
                return_dict['file'] = row['file']
                return_dict['hex_string'] = row['hex_string']
                return return_dict

def read_json_file(file):
    with open(file, 'r', encoding='utf-8') as json_data:
        return json.loads(json_data.read())

def write_file(path, filename, attr, data):
    '''Writes a string to a file.'''
    with open(f'{path}/{filename}', attr, encoding='utf-8') as open_file:
        open_file.write(data)

def __format_to_json(json_data, data, lang, number):
    '''Accepts data that is used to return a nested json.'''
    json_data[number]={}
    if data == '':
        json_data[number][f'clarity_nt_char_{number}']=f'clarity_nt_char_{number}'
    elif data == '　':
        json_data[number][f'clarity_ms_space_{number}']=f'clarity_ms_space_{number}'
    else:
        if lang == 'ja':
            json_data[number][data]=data
        else:
            json_data[number][data]=''

    return json_data

def split_hex_into_spaces(hex_str: str):
    '''
    Breaks a string up by putting spaces between every two characters.
    Used to format a hex string.
    '''
    spaced_str = " ".join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))
    return spaced_str.upper()

def setup_logger(name, log_file, func_name, level=logging.INFO):
    '''
    Sets up a logger for hook shellcode.
    '''
    formatter = logging.Formatter('%(message)s')
    handler = logging.FileHandler(log_file, encoding='utf-8')
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    if (logger.hasHandlers()):
        logger.handlers.clear()

    logger.setLevel(level)
    logger.addHandler(handler)

    return logger

def delete_folder(folder):
    '''Deletes a folder and all subfolders.'''
    try:
        shutil.rmtree(folder, ignore_errors=True)
    except:
        pass

def delete_file(file):
    '''Deletes a file.'''
    try:
        Path(file).unlink()
    except:
        pass
