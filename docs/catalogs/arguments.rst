Catalog Import Arguments
===============================================================================

This page discusses a few topics around setting up a catalog pipeline.

At a minimum, you need arguments that include where to find the input files,
the column names for RA, and DEC, and where to put the output files. 
A minimal arguments block will look something like:

.. code-block:: python

    args = ImportArguments(
        sort_columns="ObjectID",
        ra_column="ObjectRA",
        dec_column="ObjectDec",
        input_path="./my_data",
        input_format="csv",
        output_artifact_name="test_cat",
        output_path="./output",
    )


More details on each of these parameters is provided in sections below.

For the curious, see the API documentation for 
:py:class:`hipscat_import.catalog.arguments.ImportArguments`, and its superclass
:py:class:`hipscat_import.runtime_arguments.RuntimeArguments`.

Pipeline setup
-------------------------------------------------------------------------------

Dask
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

We will either use a user-provided dask ``Client``, or create a new one with
arguments:

``dask_tmp`` - ``str`` - directory for dask worker space. this should be local to
the execution of the pipeline, for speed of reads and writes. For much more 
information, see :doc:`temp_files`

``dask_n_workers`` - ``int`` - number of workers for the dask client. Defaults to 1.

``dask_threads_per_worker`` - ``int`` - number of threads per dask worker. Defaults to 1.

If you find that you need additional parameters for your dask client (e.g are creating
a SLURM worker pool), you can instead create your own dask client and pass along 
to the pipeline, ignoring the above arguments. This would look like:

.. code-block:: python

    from dask.distributed import Client
    from hipscat_import.pipeline import pipeline_with_client

    args = ...
    with Client('scheduler:port') as client:
        pipeline_with_client(args, client)

If you're running within a ``.py`` file, we recommend you use a ``main`` guard to
potentially avoid some python threading issues with dask:

.. code-block:: python

    from hipscat_import.pipeline import pipeline

    def import_pipeline():
        args = ...
        pipeline(args)

    if __name__ == '__main__':
        import_pipeline()

Resuming
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The import pipeline has the potential to be a very long-running process, if 
you're importing large amounts of data, or performing complex transformations
on the data before writing.

While the pipeline runs, we take notes of our progress so that the pipeline can
be resumed at a later time, if the job is pre-empted or canceled for any reason.

When instantiating a pipeline, you can use the ``resume`` flag to indicate that
we can resume from an earlier execution of the pipeline.

If any resume files are found, we will only proceed if you've set the ``resume=True``.
Otherwise, the pipeline will terminate.

To address this, go to the temp directory you've specified and remove any intermediate
files created by the previous runs of the ``hipscat-import`` pipeline.

Reading input files
-------------------------------------------------------------------------------

Catalog import reads through a list of files and converts them into a hipscatted catalog.


Which files?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

There are a few ways to specify the files to read:

* ``input_path`` + ``input_format``: 
    will search for files ending with the format string in the indicated directory.
* ``input_file_list``: 
    a list of fully-specified paths you want to read.

    * this strategy can be useful to first run the import on a single input
      file and validate the input, then run again on the full input set, or 
      to debug a single input file with odd behavior. 
    * if you have a mix of files in your target directory, you can use a glob
      statement like the following to gather input files:

.. code-block:: python

    in_file_paths = glob.glob("/data/object_and_source/object**.csv")
    in_file_paths.sort()

How to read them?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Specify an instance of ``InputReader`` for the ``file_reader`` parameter.

We use the ``InputReader`` class to read files in chunks and pass the chunks
along to the map/reduce stages. We've provided reference implementations for 
reading CSV, FITS, and Parquet input files, but you can subclass the reader 
type to suit whatever input files you've got.

You only need to provide the ``file_reader`` argument if you are using a custom file reader
or passing parameters to the file reader. For example you might use ``file_reader=CsvReader(separator="\s+")``
to parse a whitespace separated file.

You can find the full API documentation for 
:py:class:`hipscat_import.catalog.file_readers.InputReader`

.. code-block:: python

    class StarrReader(InputReader):
        """Class for fictional Starr file format."""
        def __init__(self, chunksize=500_000, **kwargs):
            self.chunksize = chunksize
            self.kwargs = kwargs

        def read(self, input_file):
            starr_file = starr_io.read_table(input_file, **self.kwargs)
            for smaller_table in starr_file.to_batches(max_chunksize=self.chunksize):
                smaller_table = filter_nonsense(smaller_table)
                yield smaller_table.to_pandas()

        def provenance_info(self) -> dict:
            provenance_info = {
                "input_reader_type": "StarrReader",
                "chunksize": self.chunksize,
            }
            return provenance_info

    ...

    args = ImportArguments(
        ...
        ## Locates files like "/directory/to/files/**starr"
        input_path="/directory/to/files/",
        input_format="starr",
        ## NB - you need the parens here!
        file_reader=StarrReader(),

    )

If you're reading from cloud storage, or otherwise have some filesystem credential
dict, put those in ``input_storage_options``.

Which fields?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Specify the ``ra_column`` and ``dec_column`` for the dataset.

There are two fields that we require in order to make a valid hipscatted
catalog, the right ascension and declination. At this time, this is the only 
supported system for celestial coordinates.

If you're importing data that has previously been hipscatted, you may use
``use_hipscat_index = True``. This will use that previously compused hipscat spatial
index as the position, instead of ra/dec.

Healpix order and thresholds
-------------------------------------------------------------------------------

When creating a new catalog through the hipscat-import process, we try to 
create partitions with approximately the same number of rows per partition. 
This isn't perfect, because the sky is uneven, but we still try to create 
smaller-area pixels in more dense areas, and larger-area pixels in less dense 
areas. 

We use the argument ``pixel_threshold`` and will split a partition into 
smaller healpix pixels until the number of rows is smaller than ``pixel_threshold``.
We will only split by healpix pixels up to the ``highest_healpix_order``. If we
would need to split further, we'll throw an error at the "Binning" stage, and you 
should adjust your parameters.

For more discussion of the ``pixel_threshold`` argument and a strategy for setting
this parameter, see notebook :doc:`/notebooks/estimate_pixel_threshold`

For more discussion of the "Binning" and all other stages, see :doc:`temp_files`

Alternatively, you can use the ``constant_healpix_order`` argument. This will 
**ignore** both of the ``pixel_threshold`` and ``highest_healpix_order`` arguments
and the catalog will be partitioned by healpix pixels at the
``constant_healpix_order``. This can be useful for very sparse datasets.

Progress Reporting
-------------------------------------------------------------------------------

By default, we will display some progress bars during pipeline execution. To 
disable these (e.g. when you expect no output to standard out), you can set
``progress_bar=False``.

There are several stages to the pipeline execution, and you can expect progress
reporting to look like the following:

.. code-block::

    Mapping  : 100%|██████████| 72/72 [58:55:18<00:00, 2946.09s/it]
    Binning  : 100%|██████████| 1/1 [01:15<00:00, 75.16s/it]
    Splitting: 100%|██████████| 72/72 [72:50:03<00:00, 3641.71s/it]
    Reducing : 100%|██████████| 10895/10895 [7:46:07<00:00,  2.57s/it]
    Finishing: 100%|██████████| 6/6 [08:03<00:00, 80.65s/it]

For very long-running pipelines (e.g. multi-TB inputs), you can get an 
email notification when the pipeline completes using the 
``completion_email_address`` argument. This will send a brief email, 
for either pipeline success or failure.

Output
-------------------------------------------------------------------------------

Where?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You must specify a name for the catalog, using ``output_artifact_name``.

You must specify where you want your catalog data to be written, using
``output_path``. This path should be the base directory for your catalogs, as 
the full path for the catalog will take the form of ``output_path/output_artifact_name``.

If there is already catalog data in the indicated directory, you can force a 
new catalog to be written in the directory with the ``overwrite`` flag. It's
preferable to delete any existing contents, however, as this may cause 
unexpected side effects.

If you're writing to cloud storage, or otherwise have some filesystem credential
dict, put those in ``output_storage_options``.

In addition, you can specify directories to use for various intermediate files:

- dask worker space (``dask_tmp``)
- sharded parquet files (``tmp_dir``)
- intermediate resume files (``resume_tmp``)

Most users are going to be ok with simply setting the ``tmp_dir`` for all intermediate
file use. For more information on these parameters, when you would use each, 
and demonstrations of temporary file use see :doc:`temp_files`

How?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You may want to tweak parameters of the final catalog output, and we have helper 
arguments for a few of those.

``add_hipscat_index`` - ``bool`` - whether or not to add the hipscat spatial index
as a column in the resulting catalog. The ``_hipscat_index`` field is designed to make many 
dask operations more performant, but if you do not intend to publish your dataset
and do not intend to use dask, then you can suppress generation of this column to
save a little space in your final disk usage.

The ``_hipscat_index`` uses a high healpix order and a uniqueness counter to create
values that can order all points in the sky, according to a nested healpix scheme.

``sort_columns`` - ``str`` - column for survey identifier, or other sortable column. 
If sorting by multiple columns, they should be comma-separated. 
If ``add_hipscat_index=True``, this sorting will be used to resolve the 
index counter within the same higher-order pixel space.

``use_schema_file`` - ``str`` - path to a parquet file with schema metadata. 
This will be used for column metadata when writing the files, if specified.
For more information on why you would want this file and how to generate it,
check out our notebook :doc:`/notebooks/unequal_schema`.

``debug_stats_only`` - ``bool`` - If ``True``, we will not create the leaf
parquet files with the catalog data, and will only generate root-level metadata
files representing the full statistics of the final catalog. This can be useful
when probing the import process for effectiveness on processing a target dataset.

``epoch`` - ``str`` - astronomical epoch for the data. defaults to ``"J2000"``

``catalog_type`` - ``"object"`` or ``"source"``. Indicates the level of catalog data,
using the LSST nomenclature:

- object - things in the sky (e.g. stars, galaxies)
- source - detections of things in the sky at some point in time.

Some data providers split detection-level data into a separate catalog, to make object
catalogs smaller, and reflects a relational data model.