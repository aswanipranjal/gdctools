#!/usr/bin/env python
# encoding: utf-8

# Front Matter {{{
'''
Copyright (c) 2016 The Broad Institute, Inc.  All rights are reserved.

gdc_mirror: this file is part of gdctools.  See the <root>/COPYRIGHT
file for the SOFTWARE COPYRIGHT and WARRANTY NOTICE.

@author: Timothy DeFreitas, Michael S. Noble
@date:  2016_05_25
'''

# }}}

from __future__ import print_function
import logging
import json
import csv
import os
import sys
import gzip
from pkg_resources import resource_filename #@UnresolvedImport

from lib.convert import util as convert_util
from lib.convert import seg as gdac_seg
from lib.convert import py_clinical as gdac_clin
from lib.convert import tsv2idtsv as gdac_tsv2idtsv
from lib.convert import tsv2magetab as gdac_tsv2magetab
from lib.report import draw_heatmaps
from lib import common
from lib import meta
from lib.constants import REPORT_DATA_TYPES, ANNOT_TO_DATATYPE

from GDCtool import GDCtool

class gdc_dicer(GDCtool):

    def __init__(self):
        super(gdc_dicer, self).__init__(version="0.3.0")
        cli = self.cli

        desc =  'Dice data from a Genomic Data Commons (GDC) mirror'
        cli.description = desc

        cli.add_argument('-m', '--mirror-dir',
                         help='Root folder of mirrored GDC data')
        cli.add_argument('-d', '--dice-dir',
                         help='Root of diced data tree')
        cli.add_argument('--dry-run', action='store_true',
                         help="Show expected operations, but don't perform dicing")
        cli.add_argument('timestamp', nargs='?',
                         help='Dice using metadata from a particular date.'\
                         'If omitted, the latest version will be used')
        fd_help = "Skip detection of already diced files, and redice everything"
        cli.add_argument('-f', '--force-dice',
                         action='store_true', help=fd_help)

    def parse_args(self):
        opts = self.options

        if opts.log_dir: self.dice_log_dir = opts.log_dir
        if opts.mirror_dir: self.mirror_root_dir = opts.mirror_dir
        if opts.dice_dir: self.dice_root_dir = opts.dice_dir
        if opts.programs: self.dice_programs = opts.programs
        if opts.projects: self.dice_projects = opts.projects
        self.force_dice = opts.force_dice

        # Figure out timestamp
        mirror_root = self.mirror_root_dir
        #Sets timestamp for this run
        self.timestamp = opts.timestamp
        if self.timestamp is None:
            self.timestamp = common.timetuple2stamp()

        # Discover which GDC programs & projects data will be diced
        latest_tstamps = set()
        if not self.dice_programs:
            self.dice_programs = common.immediate_subdirs(mirror_root)

        # FIXME: Dicer will only work correctly with one program, since
        # projects are not linked to which program they are from
        if not self.dice_projects:
            projects = []
            for program in self.dice_programs:
                mirror_prog_root = os.path.join(mirror_root, program)
                projects.extend(common.immediate_subdirs(mirror_prog_root))
            # Filter the metadata folders out
            self.dice_projects = [p for p in projects if p != 'metadata']


        # TODO: Verify that each of these are valid programs and fail fast

    def dice(self):
        logging.info("GDC Dicer Version: %s", self.cli.version)
        logging.info("Command: " + " ".join(sys.argv))
        mirror_root = self.mirror_root_dir
        diced_root = self.dice_root_dir
        trans_dict = build_translation_dict(resource_filename("gdctools",
                                                "config/annotations_table.tsv"))
        # Set in init_logs()
        timestamp = self.timestamp
        logging.info("Timestamp: " + timestamp)

        # Get cohort to aggregate map
        cohort_agg_dict = self.cohort_aggregates()

        # Iterable of programs, either user specified or discovered from folder names in the diced root
        if self.dice_programs:
            programs = self.dice_programs
        else:
            programs = common.immediate_subdirs(mirror_root)

        for program in programs:
            diced_prog_root = os.path.join(diced_root, program)
            mirror_prog_root = os.path.join(mirror_root, program)

            # Ensure no simultaneous mirroring/dicing
            with common.lock_context(diced_prog_root, "dice"), \
                 common.lock_context(mirror_prog_root, "mirror"):
                projects = self.dice_projects

                agg_case_data = dict()

                for project in sorted(projects):
                    # Load metadata from mirror, getting the latest metadata
                    # earlier than the given timestamp
                    raw_project_root = os.path.join(mirror_prog_root, project)
                    meta_dir = os.path.join(raw_project_root, "metadata")
                    meta_dirs = [d for d in os.listdir(meta_dir) if d <= timestamp]

                    # Check to see if there is actually metadata available,
                    # and skip with a warning if not
                    if len(meta_dirs) < 1:
                        _warning =  "No metadata found for " + project
                        _warning += " earlier than " + timestamp
                        logging.warning(_warning)
                        continue


                    latest_meta = os.path.join(meta_dir, sorted(meta_dirs)[-1])
                    metadata = meta.latest_metadata(latest_meta)

                    diced_project_root = os.path.join(diced_prog_root, project)
                    logging.info("Dicing " + project + " to " + diced_project_root)

                    # The natural form of the metadata is a list of file dicts,
                    # which makes it easy to mirror on a project by project
                    # basis. However, the dicer should insist that only one
                    # file per case per annotation exists, and therefore we must
                    # generate a data structure in this form by iterating over
                    # the metadata before dicing.

                    tcga_lookup = _tcgaid_file_lookup(metadata, trans_dict)

                    # Diced Metadata
                    diced_meta_dir = os.path.join(diced_project_root,
                                                  "metadata", timestamp)
                    diced_meta_fname = ".".join([project, timestamp,
                                                'diced_metadata', 'tsv'])
                    if not os.path.isdir(diced_meta_dir):
                        os.makedirs(diced_meta_dir)
                    meta_file = os.path.join(diced_meta_dir, diced_meta_fname)

                    # Count project annotations
                    with open(meta_file, 'w') as mf:
                        # Header
                        META_HEADERS = ['case_id', 'tcga_barcode', 'sample_type',
                                        'annotation', 'file_name', 'center',
                                        'platform', 'report_type']
                        mfw = csv.DictWriter(mf, fieldnames=META_HEADERS,
                                             delimiter='\t')
                        mfw.writeheader()

                        for tcga_id in tcga_lookup:
                            for annot, file_d in tcga_lookup[tcga_id].iteritems():
                                dice_one(file_d, trans_dict, raw_project_root,
                                         diced_project_root, mfw,
                                         dry_run=self.options.dry_run,
                                         force=self.force_dice)

                    # Bookkeeping code -- write some useful tables
                    # and figures needed for downstream sample reports.
                    # Count available data per sample
                    logging.info("Generating counts for " + project)
                    case_data = _case_data(meta_file)
                    counts_file = ".".join([project, timestamp, "sample_counts.tsv"])
                    counts_file = os.path.join(diced_meta_dir, counts_file)
                    _write_counts(case_data, project, counts_file)

                    # Heatmaps per sample
                    logging.info("Generating heatmaps for " + project)
                    create_heatmaps(case_data, project, timestamp, diced_meta_dir)

                    # keep track of aggregate case data
                    project_aggregates = cohort_agg_dict[project]
                    for agg in project_aggregates:
                        agg_case_data[agg] = agg_case_data.get(agg, {})
                        agg_case_data[agg].update(case_data)

                # Create aggregate diced_metadata.tsvs
                self.aggregate_diced_metadata(diced_prog_root, timestamp)

                # As well as aggregate counts and heatmaps
                for agg in agg_case_data:
                    ac_data = agg_case_data[agg]
                    meta_dir = os.path.join(diced_prog_root, agg,
                                              "metadata", timestamp)

                    logging.info("Generating aggregate counts for " + agg)
                    counts_file = ".".join([agg, timestamp, "sample_counts.tsv"])
                    counts_file = os.path.join(meta_dir, counts_file)
                    _write_counts(ac_data, agg, counts_file)

                    logging.info("Generating aggregate heatmaps for " + agg)
                    create_heatmaps(ac_data, agg, timestamp, meta_dir)

        logging.info("Dicing completed successfuly")

    def execute(self):
        super(gdc_dicer, self).execute()
        opts = self.options
        self.parse_args()
        common.init_logging(self.timestamp, self.dice_log_dir, "gdcDice")
        try:
            logging.info(self.aggregates)
            self.dice()
        except Exception as e:
            logging.exception("Dicing FAILED:")

    def cohort_aggregates(self):
        '''Invert the Aggregate->Cohort dictionary to list all aggregates for
        a cohort.'''
        cohort_agg = dict()
        for k, v in self.aggregates.iteritems():
            cohorts = v.split(',')
            for c in cohorts:
                cohort_agg[c] = cohort_agg.get(c, []) + [k]
        self.c_aggregates = cohort_agg
        return cohort_agg

    def aggregate_diced_metadata(self, prog_dir, timestamp):
        '''Aggregates the diced metadata files for aggregates'''
        aggregates = self.aggregates
        for agg, cohorts in aggregates.iteritems():
            cohorts = sorted(cohorts.split(','))
            agg_meta_folder = os.path.join(prog_dir, agg, "metadata", timestamp)
            if not os.path.isdir(agg_meta_folder):
                os.makedirs(agg_meta_folder)
            agg_meta_file = ".".join([agg, timestamp, 'diced_metadata', 'tsv'])
            agg_meta_file = os.path.abspath(os.path.join(agg_meta_folder,
                                                        agg_meta_file))
            skip_header = False
            with open(agg_meta_file, 'w') as out:
                for c in cohorts:
                    c_meta_folder = os.path.join(prog_dir, c, "metadata", timestamp)
                    c_meta_file = ".".join([c, timestamp, 'diced_metadata', 'tsv'])
                    c_meta_file = os.path.abspath(os.path.join(c_meta_folder,
                                                               c_meta_file))
                    with open(c_meta_file, 'r') as f_in:
                        if skip_header:
                            f_in.next()
                        for line in f_in:
                            out.write(line)
                    skip_header = True

def _tcgaid_file_lookup(metadata, translation_dict):
    '''Builds a dictionary mapping tcga_ids to their file info,
    stratified by annotation type. This enables the dicer to ensure one diced
    file per sample or case'''
    d = dict()
    for file_dict in metadata:
        tcga_id = meta.tcga_id(file_dict)
        annot, _ = get_annotation_converter(file_dict, translation_dict)
        d[tcga_id] = d.get(tcga_id, dict())
        # Note that this overwrites any previous value.
        # FIXME: More sophisticated reasoning
        d[tcga_id][annot] =  file_dict

    return d

def build_translation_dict(translation_file):
    """Builds a translation dictionary from a translation table.

    First column of the translation_file is the Annotation name,
    remaining columns are signatures in the file metadata that indicate a file is of this annotation type.
    """

    with open(translation_file, 'rU') as tsvfile:
        reader = csv.DictReader(tsvfile, delimiter='\t')
        d = dict()

        # Duplicate detection
        dupes = False
        for row in reader:
            annot = row.pop("Firehose_annotation")
            converter_name = row.pop("converter")

            ## Parse list fields into frozensets
            row['tags'] = frozenset(row['tags'].split(',') if row['tags'] != '' else [])

            # Only add fields from the row if they are present in the row_dict
            # Give a warning if overwriting an existing tag, and don't add the new one
            key = frozenset(row.items())
            if key not in d:
                d[key] = (annot, converter(converter_name))
            else:
                dupes = True
    if dupes:
        logging.warning("duplicate annotation definitions detected")
    return d

def dice_one(file_dict, translation_dict, mirror_proj_root, diced_root,
             meta_file_writer, dry_run=False, force=False):
    """Dice a single file from a GDC mirror.

    Diced data will be placed in /<diced_root>/<annotation>/. If dry_run is
    true, a debug message will be displayed instead of performing the actual
    dicing operation.
    """
    mirror_path = meta.mirror_path(mirror_proj_root, file_dict)
    if os.path.isfile(mirror_path):
        ## Get the right annotation and converter for this file
        annot, convert = get_annotation_converter(file_dict, translation_dict)
        # FIXME: Handle this better
        if annot != 'UNRECOGNIZED':
            dice_path = os.path.join(diced_root, annot)
            # convert expected path to a relative path from the diced_root
            expected_path = convert_util.diced_file_path(dice_path, file_dict)
            expected_path = os.path.abspath(expected_path)
            logging.info("Dicing file {0} to {1}".format(mirror_path,
                                                         expected_path))
            if not dry_run:
                # Dice if force_dice is enabled or the file doesn't exist
                if force or not os.path.isfile(expected_path):
                    convert(file_dict, mirror_path, dice_path)

                append_diced_metadata(file_dict, expected_path,
                                      annot, meta_file_writer)
        else:
            logging.warn('Unrecognized data:\n%s' % json.dumps(file_dict,
                                                               indent=2))

def get_annotation_converter(file_dict, translation_dict):
    k = metadata_to_key(file_dict)
    if k in translation_dict:
        return translation_dict[k]
    else:
        # FIXME: Gracefully handle this instead of creating a new annotation type
        return "UNRECOGNIZED", None

def metadata_to_key(file_dict):
    """Converts the file metadata in file_dict into a key in the TRANSLATION_DICT"""
    # Required fields
    data_type = file_dict.get("data_type", '')
    data_category = file_dict.get("data_category", '')
    experimental_strategy = file_dict.get("experimental_strategy", '')
    platform = file_dict.get("platform", '')
    tags = _parse_tags(file_dict.get("tags",[]))
    center_namespace = file_dict['center']['namespace'] if 'center' in file_dict else ''
    workflow_type = file_dict['analysis']['workflow_type'] if 'analysis' in file_dict else ''

    return frozenset({
        "data_type" : data_type,
        "data_category": data_category,
        "experimental_strategy": experimental_strategy,
        "platform": platform,
        "tags": tags,
        "center_namespace": center_namespace,
        "workflow_type" : workflow_type
    }.items())

def append_diced_metadata(file_dict, diced_path, annot, meta_file_writer):
    '''Write a row for the given file_dict using meta_file_writer.

    meta_file_writer must be a csv.DictWriter
    '''
    sample_type = None
    if meta.has_sample(file_dict):
        sample_type = meta.sample_type(file_dict)

    # Write row with csv.DictWriter.writerow()
    meta_file_writer.writerow({
        'case_id'      : meta.case_id(file_dict),
        'tcga_barcode' : meta.tcga_id(file_dict),
        'sample_type'  : sample_type,
        'annotation'   : annot,
        'file_name'    : diced_path,
        'center'       : meta.center(file_dict),
        'platform'     : meta.platform(file_dict),
        'report_type'  : ANNOT_TO_DATATYPE[annot]
    })

def _case_data(diced_metadata_file):
    '''Create a case-based lookup of available data types'''
    # Use a case-based dictionary to count each data type on a case/sample basis
    # cases[<case_id>][<sample_type>] = set([REPORT_DATA_TYPE, ...])
    cases = dict()
    cases_with_clinical = set()
    cases_with_biospecimen = set()

    with open(diced_metadata_file, 'r') as dmf:
        reader = csv.DictReader(dmf, delimiter='\t')
        # Loop through counting non-case-level annotations
        for row in reader:
            annot = row['annotation']
            case_id = row['case_id']
            report_dtype = ANNOT_TO_DATATYPE[annot]

            if report_dtype == 'BCR':
                cases_with_clinical.add(case_id)
            elif report_dtype == 'Clinical':
                cases_with_biospecimen.add(case_id)
            else:
                _, sample_type = meta.tumor_code(row['sample_type'])
                case_dict = cases.get(case_id, {})
                case_dict[sample_type] = case_dict.get(sample_type, set())
                case_dict[sample_type].add(report_dtype)
                cases[case_id] = case_dict

    # Now go back through and add BCR & Clinical to all sample_types
    for c in cases:
        case_dict = cases[c]
        for st in case_dict:
            if c in cases_with_clinical:
                case_dict[st].add('Clinical')
            if c in cases_with_biospecimen:
                case_dict[st].add('BCR')

    return cases

def _write_counts(case_data, proj_name, f):
    '''Write case data as counts '''
    # First, put the case data into an easier format:
    # { 'TP' : {'BCR' : 10, '...': 15, ...},
    #   'TR' : {'Clinical' : 10, '...': 15, ...},
    #           ...}
    counts = dict()
    for case in case_data:
        c_dict = case_data[case]
        for sample_type in c_dict:
            counts[sample_type] = counts.get(sample_type, {})
            for report_type in c_dict[sample_type]:
                counts[sample_type][report_type] = counts[sample_type].get(report_type, 0) + 1

    # Now write the counts table
    rdt = REPORT_DATA_TYPES
    with open(f, 'w') as out:
        # Write header
        out.write("Sample Type\t" + "\t".join(rdt) + '\n')
        for code in counts:
            line = code + "\t"
            # Headers can use abbreviated data types
            line += "\t".join([str(counts[code].get(t, 0)) for t in rdt]) + "\n"

            out.write(line)

        # Write totals. Totals is dependent on the main analyzed tumor type
        main_code = meta.tumor_code(meta.main_tumor_sample_type(proj_name))[1]
        tots = [str(counts.get(main_code,{}).get(t, 0)) for t in rdt]
        out.write('Totals\t' + '\t'.join(tots) + "\n")

def create_heatmaps(case_data, project, timestamp, diced_meta_dir):
    rownames, matrix = _build_heatmap_matrix(case_data)
    draw_heatmaps(rownames, matrix, project, timestamp, diced_meta_dir)

def _build_heatmap_matrix(case_data):
    '''Build a 2d matrix and rownames from annotations and load dict'''
    rownames = REPORT_DATA_TYPES
    annot_sample_data = dict()
    for case in case_data:
        c_dict = case_data[case]
        # Flatten case_data[case_id][sample_type] = set(Data types)
        # into annot_sample_data[case_id] = set(Data types)
        # for simpler heatmap
        data_types = {dt for st in c_dict for dt in c_dict[st]}
        annot_sample_data[case] = data_types

    matrix = [[] for row in rownames]
    # Now iterate over samples, inserting a 1 if data is presente
    for r in range(len(rownames)):
        for cid in sorted(annot_sample_data.keys()):
            # append 1 if data is present, else 0
            matrix[r].append( 1 if rownames[r] in annot_sample_data[cid] else 0)

    return rownames, matrix

## Converter mappings
def converter(converter_name):
    """Returns the converter function by name using a dictionary lookup."""
    CONVERTERS = {
        'clinical' : clinical,
        'copy' : copy,
        'magetab_data_matrix': magetab_data_matrix,
        'maf': maf,
        'seg_broad': seg_broad,
        'seg_harvard': seg_harvard,
        'seg_harvardlowpass': seg_harvardlowpass,
        'seg_mskcc2' : seg_mskcc2,
        'tsv2idtsv' : tsv2idtsv,
        'unzip_tsv2idtsv': unzip_tsv2idtsv,
        'tsv2magetab': tsv2magetab,
        'unzip_tsv2magetab': unzip_tsv2magetab,
        'fpkm2magetab': fpkm2magetab,
        'unzip_fpkm2magetab': unzip_fpkm2magetab
    }

    return CONVERTERS[converter_name]

# Converters
# Each must return a dictionary mapping case_ids to the diced file paths
def copy(file_dict, mirror_path, dice_path):
    print("Dicing with 'copy'")
    pass

def clinical(file_dict, mirror_path, outdir):
    case_id = meta.case_id(file_dict)
    return {case_id: gdac_clin.process(mirror_path, file_dict, outdir)}

def maf(file_dict, mirror_path, dice_path):
    pass

def magetab_data_matrix(file_dict, mirror_path, dice_path):
    pass

def seg_broad(file_dict, mirror_path, dice_path):
    infile = mirror_path
    hyb_id = file_dict['file_name'].split('.',1)[0]
    tcga_id = meta.aliquot_id(file_dict)
    case_id = meta.case_id(file_dict)
    return {case_id: gdac_seg.process(infile, file_dict, hyb_id,
                                      tcga_id, dice_path, 'seg_broad')}

def seg_harvard(file_dict, mirror_path, dice_path):
    pass
def seg_harvardlowpass(file_dict, mirror_path, dice_path):
    pass
def seg_mskcc2(file_dict, mirror_path, dice_path):
    pass

def tsv2idtsv(file_dict, mirror_path, dice_path):
    case_id = meta.case_id(file_dict)
    return {case_id : gdac_tsv2idtsv.process(mirror_path, file_dict, dice_path)}

def unzip_tsv2idtsv(file_dict, mirror_path, dice_path):
    return _unzip(file_dict, mirror_path, dice_path, tsv2idtsv)

def tsv2magetab(file_dict, mirror_path, dice_path):
    case_id = meta.case_id(file_dict)
    return {case_id : gdac_tsv2magetab.process(mirror_path, file_dict,
                                               dice_path)}

def unzip_tsv2magetab(file_dict, mirror_path, dice_path):
    return _unzip(file_dict, mirror_path, dice_path, tsv2magetab)

def fpkm2magetab(file_dict, mirror_path, dice_path):
    case_id = meta.case_id(file_dict)
    return {case_id : gdac_tsv2magetab.process(mirror_path, file_dict,
                                               dice_path, fpkm=True)}

def unzip_fpkm2magetab(file_dict, mirror_path, dice_path):
    return _unzip(file_dict, mirror_path, dice_path, fpkm2magetab)

def _unzip(file_dict, mirror_path, dice_path, _converter):
    # First unzip the mirror_path, which is a .gz
    if not mirror_path.endswith('.gz'):
        raise ValueError('Unexpected gzip filename: ' +
                         os.path.basename(mirror_path))
    uncompressed = mirror_path.rstrip('.gz')
    with gzip.open(mirror_path, 'rb') as mf, open(uncompressed, 'w') as out:
        out.write(mf.read())
    # Now dice extracted file
    diced = _converter(file_dict, uncompressed, dice_path)
    # Remove extracted file to save disk space
    os.remove(uncompressed)
    return diced

def _parse_tags(tags_list):
    return frozenset('' if len(tags_list)==0 else tags_list)

if __name__ == "__main__":
    gdc_dicer().execute()
