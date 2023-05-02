"""
Entry point to all the scripts.
"""

import argparse
from re import T
from xmlrpc.client import _iso8601_format
import pandas as pd
import sys, shutil, os
import subprocess
from phonemizer import phonemize
from phonemizer.separator import Separator
from pathlib import Path
from aochildes.dataset import AOChildesDataSet
from aochildes.params import AOChildesParams
from aochildes.configs import Dirs

# Espeak has some issues with joining IPA symbols together, so we need to add spaces between them
REPLACE_DICT = {'ɛɹ': 'ɛ ɹ', 
                'ʊɹ' : 'ʊ ɹ',
                'əl' : 'ə l',
                'oːɹ' : 'oː ɹ',
                'ɪɹ' : 'ɪ ɹ',
                'ɑːɹ' : 'ɑː ɹ',
                'ɔːɹ' : 'ɔː ɹ',
                'aɪɚ' : 'aɪ ɚ',
                'iə' : 'i ə',
                'aɪə' : 'aɪ ə',
                'aɪʊɹ' : 'aɪ ʊ ɹ',
                'aɪʊ' : 'aɪ ʊ',
                'dʒ' : 'd̠ʒ',
                'tʃ' : 't̠ʃ'}

def download(args):
    """ Downloads utterances from CHILDES using `childespy`"""

    # TODO: Add ability to download from entire collection (e.g. Eng-NA)

    print(f'\n\nAttempting to get utterances from the "{args.corpus}" corpus in the "{args.collection}" collection:\n')
    utts = get_utterances(collection=args.collection, corpus=args.corpus)
    speakers = list(utts["target_child_name"].unique())
    path = args.out_path / f'{args.collection}'

    if args.separate_by_child:
        path = path / f'{args.corpus}'
        if not path.exists():
            path.mkdir(parents=True)
        for speaker in speakers:
            a = utts[utts["target_child_name"] == speaker]
            out_path = path /f'{speaker}.csv'
            if out_path.exists():
                out_path.unlink()
            print(f'Saving {len(a)} utterances to {out_path}')
            a.to_csv(out_path)
    else:
        if not path.exists():
            path.mkdir(parents=True)
        out_path = path / f'{args.corpus}.csv'
        utts.to_csv(out_path)
        print(f'Saving {len(utts)} utterances to {out_path}')

def extract(args):
    """ 
    If the path points to a CHILDES corpora CSV file, extracts the child and child-directed utterances
    and places them in a new folder with the same name as the CSV file. 
    If the path points to a folder, extracts the child and child-directed utterances in every CSV
    file found in that directory and concatenates them.
    """

    path = args.path
    out_path = args.out_path
    transcripts = path
    if path.is_dir():
        print('Path is a directory, will extract utterances from all CSVs found in this directory.')
    else:
        print('Path is a CSV file, will only extract utterances from this file.')
        # Temporarily copy csv file to a new folder since AOChildes takes a folder for processing
        transcripts = path.parent / ('tmp_' + path.stem)
        print(f'Creating temporary directory {transcripts}')
        transcripts.mkdir(exist_ok=True)
        shutil.copy(path, transcripts)

    csvs = [p for p in list(transcripts.iterdir()) if p.suffix == '.csv']
    print(f'Found {len(csvs)} CSVs in {transcripts}')
    collection_names = []
    for csv in csvs:
        a = pd.read_csv(csv)
        collection_names.extend(list(a.collection_name.unique()))
    collection_names = list(set(collection_names))
    print(f'Found collection names: {collection_names}')

    # Have AOChildes use our saved transcripts
    Dirs.transcripts = transcripts

    # Using the pre-processing from aochildes to extract child and adult utterances
    print('\n--Using AOChildes to extract adult utterances:--')
    adult_data = AOChildesDataSet(AOChildesParams(collection_names=collection_names, max_days=24))
    adult_utterances = adult_data.load_sentences()
    print(f'--Number of adult utterances: {len(adult_utterances)}--')

    # Exclude all adult utterances from child data, as well as non-target-child utterances
    all_speakers = adult_data.pipeline.df.groupby("speaker_role").size()
    all_but_target_child = list(all_speakers.keys()) + ['Child']

    print('\n--Using AOChildes to extract child utterances:--')
    child_data = AOChildesDataSet(AOChildesParams(bad_speaker_roles=all_but_target_child, collection_names=collection_names, max_days=24)) # AO-CHILDES calls it max_days, but it's now actually months
    child_utterances = child_data.load_sentences()
    print(f'--Number of child utterances: {len(child_utterances)}--\n')

    out_path.mkdir(exist_ok=True, parents=True)
    child_out = out_path / 'child.txt'
    adult_out = out_path / 'adult.txt'
    open(child_out, 'w').writelines('\n'.join(child_utterances))
    print(f'Wrote child utterances to: {child_out}')
    open(adult_out, 'w').writelines('\n'.join(adult_utterances))
    print(f'Wrote adult utterances to: {adult_out}')

    if not path.is_dir():
        copied_file = transcripts / path.name
        copied_file.unlink()
        print(f'Deleting temporary directory {transcripts}')
        os.rmdir(transcripts)

def phonemize_file(args):
    """ Uses phonemizer to phonemize a text """

    if args.split and not args.out_path.is_dir():
        print('WARNING: When splitting, a directory must be provided. Using file name as directory name')
        args.out_path = args.out_path.parent / args.out_path.stem
        args.out_path.mkdir(exist_ok=True)
    if not args.split and args.out_path.is_dir():
        print('WARNING: When not splitting, output path should be a file, not a directory. Adding .txt to path.')
        args.out_path = args.out_path.parent / (str(args.out_path.stem) + '.txt')
        args.out_path.parent.mkdir(exist_ok=True)

    lines = open(args.path, 'r').readlines()

    print(f'Phonemizing using language {args.language}...')
    phn = phonemize(
        lines,
        language=args.language,
        backend='espeak',
        separator=Separator(phone='PHONE_BOUNDARY', word=' ', syllable=''),
        strip=True,
        preserve_punctuation=False,
        language_switch='remove-utterance',
        words_mismatch='remove',
        njobs=4)
    
    mismatched = len([line for line in phn if line == ''])
    phn = [line.replace(' ', ' WORD_BOUNDARY ').replace('PHONE_BOUNDARY', ' ') for line in phn if line != ''] # Set the word boundary
    # Use replace map
    for key, value in REPLACE_DICT.items():
        phn = [line.replace(key, value) for line in phn]
    phn = [line + ' WORD_BOUNDARY \n' for line in phn] # Add newline

    print(f'Removed {mismatched} mismatched or language switched lines')

    # print(f'Phonemizing using language "{args.language}". This may take a few minutes...')
    # phn = subprocess.check_output(['espeak', '-f', args.path, '-q', '-x', '--ipa', '-v', args.language, '--sep']).decode('utf-8').split('\n')
    # phn_filtered = []
    # dropped_lines = 0
    # for i in range(len(lines)):
    #     line = phn[i]
    #     if '(' in line or ')' in line or line == '':
    #         dropped_lines += 1
    #         continue
    #     line = line.replace('  ', ' WORD_BOUNDARY ') # Set the word boundary
    #     line = line.replace("ˈ", "") # Remove stress marking
    #     line = line.replace("ˌ", "") # Remove secondary stress marking
    #     line = line + ' WORD_BOUNDARY \n' # Add newline
    #     phn_filtered.append(line)
    # phn = phn_filtered

    # print(f'Dropped {dropped_lines} lines due to mismatch with original text')

    if args.split:
        train_lines = []
        valid_lines = []
        test_lines = []
        # Split the lines 90-5-5 while preserving age-ordering
        for i, line in enumerate(phn):
            if i % 20 == 18:
                valid_lines.append(line)
            elif i % 20 == 19:
                test_lines.append(line)
            else:
                train_lines.append(line)
        print(f'Total lines: {len(phn)}')
        open(args.out_path / 'train.txt', 'w').writelines(train_lines)
        print(f'Wrote {len(train_lines)} ({round(len(train_lines)/len(phn), 3)*100}%) lines to {args.out_path / "train.txt"}')
        open(args.out_path / 'valid.txt', 'w').writelines(valid_lines)
        print(f'Wrote {len(valid_lines)} ({round(len(valid_lines)/len(phn), 3)*100}%) lines to {args.out_path / "valid.txt"}')
        open(args.out_path / 'test.txt', 'w').writelines(test_lines)
        print(f'Wrote {len(test_lines)} ({round(len(test_lines)/len(phn), 3)*100}%) lines to {args.out_path / "test.txt"}')
    else:
        open(args.out_path, 'w').writelines(phn)
        print(f'Wrote {len(phn)} lines to {args.out_path}')

parser = argparse.ArgumentParser(description="Childes Processor")
subparsers = parser.add_subparsers(help='sub-command help')
parser_download = subparsers.add_parser('download', help='Download utterances from CHILDES into a CSV')
parser_download.add_argument('corpus', help='Name of the corpus to download')
parser_download.add_argument('collection', help='Name of the collection that the corpus is contained within')
parser_download.add_argument('-o', '--out_path', default='childes', type=Path, help='Directory to save utterances to')
parser_download.add_argument('-s', '--separate_by_child', action='store_true', help='Create a separate output file for each child in the corpus')
parser_download.set_defaults(func=download)

parser_extract = subparsers.add_parser('extract', help='Extract utterances from a CSV, separating child and child-directed speech')
parser_extract.add_argument('path', type=Path, help='CSV file of utterances or folder of utterances to extract from')
parser_extract.add_argument('-o', '--out_path', default='processed', type=Path, help='Directory to save utterances to')
parser_extract.set_defaults(func=extract)

parser_phonemize = subparsers.add_parser('phonemize', help='Takes a txt file of utterances and returns a phonemized file')
parser_phonemize.add_argument('path', type=Path, help='Text file containing the utterances to phonemize')
# TODO: limit language to certain options or test if ok
parser_phonemize.add_argument('language', type=str, help='Language used to phonemize')
parser_phonemize.add_argument('-o', '--out_path', default='phonemized.txt', type=Path, help='File or directory to save utterances to')
parser_phonemize.add_argument('-s', '--split', action='store_true', help='Produce three files according to a train-valid-test split of 90-5-5. Splitting is interleaved, not sequential.')
parser_phonemize.set_defaults(func=phonemize_file)

args = parser.parse_args()
if args.func == download:
    # Only import childespy if downloading from CHILDES, since it re-downloads childesr each time
    print ('Importing childespy')
    from childespy import get_utterances
args.func(args)
