"""Import a set of non-hipscat files using dask for parallelization"""

from typing import Any, Dict, Union

import healpy as hp
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from hipscat import pixel_math
from hipscat.io import FilePointer, file_io, paths
from hipscat.pixel_math.hipscat_id import HIPSCAT_ID_COLUMN, hipscat_id_to_healpix

from hipscat_import.catalog.file_readers import InputReader
from hipscat_import.catalog.resume_plan import ResumePlan

# pylint: disable=too-many-locals,too-many-arguments


def _get_pixel_directory(cache_path: FilePointer, order: np.int64, pixel: np.int64):
    """Create a path for intermediate pixel data.

    This will take the form:

        <cache_path>/dir_<directory separator>/pixel_<pixel>

    where the directory separator is calculated using integer division:

        (pixel/10000)*10000

    and exists to mitigate problems on file systems that don't support
    more than 10_000 children nodes.
    """
    dir_number = int(pixel / 10_000) * 10_000
    return file_io.append_paths_to_pointer(
        cache_path, f"order_{order}", f"dir_{dir_number}", f"pixel_{pixel}"
    )


def _has_named_index(dataframe):
    """Heuristic to determine if a dataframe has some meaningful index.

    This will reject dataframes with no index name for a single index,
    or empty names for multi-index (e.g. [] or [None]).
    """
    if dataframe.index.name is not None:
        ## Single index with a given name.
        return True
    if len(dataframe.index.names) == 0 or all(name is None for name in dataframe.index.names):
        return False
    return True


def _iterate_input_file(
    input_file: FilePointer,
    file_reader: InputReader,
    highest_order,
    ra_column,
    dec_column,
    use_hipscat_index=False,
):
    """Helper function to handle input file reading and healpix pixel calculation"""
    if not file_reader:
        raise NotImplementedError("No file reader implemented")

    required_columns = [ra_column, dec_column]

    for chunk_number, data in enumerate(file_reader.read(input_file)):
        if use_hipscat_index:
            if data.index.name == HIPSCAT_ID_COLUMN:
                mapped_pixels = hipscat_id_to_healpix(data.index, target_order=highest_order)
            elif HIPSCAT_ID_COLUMN in data.columns:
                mapped_pixels = hipscat_id_to_healpix(data[HIPSCAT_ID_COLUMN], target_order=highest_order)
            else:
                raise ValueError(
                    f"Invalid column names in input file: {HIPSCAT_ID_COLUMN} not in {input_file}"
                )
        else:
            if not all(x in data.columns for x in required_columns):
                raise ValueError(
                    f"Invalid column names in input file: {', '.join(required_columns)} not in {input_file}"
                )
            # Set up the pixel data
            mapped_pixels = hp.ang2pix(
                2 ** highest_order,
                data[ra_column].values,
                data[dec_column].values,
                lonlat=True,
                nest=True,
            )
        yield chunk_number, data, mapped_pixels


def map_to_pixels(
    input_file: FilePointer,
    file_reader: InputReader,
    resume_path: FilePointer,
    mapping_key,
    highest_order,
    ra_column,
    dec_column,
    use_hipscat_index=False,
):
    """Map a file of input objects to their healpix pixels.

    Args:
        input_file (FilePointer): file to read for catalog data.
        file_reader (hipscat_import.catalog.file_readers.InputReader): instance of input
            reader that specifies arguments necessary for reading from the input file.
        resume_path (FilePointer): where to write resume partial results.
        mapping_key (str): unique counter for this input file, used
            when creating intermediate files
        highest_order (int): healpix order to use when mapping
        ra_column (str): where to find right ascension data in the dataframe
        dec_column (str): where to find declation in the dataframe

    Returns:
        one-dimensional numpy array of long integers where the value at each index corresponds
        to the number of objects found at the healpix pixel.
    Raises:
        ValueError: if the `ra_column` or `dec_column` cannot be found in the input file.
        FileNotFoundError: if the file does not exist, or is a directory
    """
    histo = pixel_math.empty_histogram(highest_order)
    for _, _, mapped_pixels in _iterate_input_file(
        input_file, file_reader, highest_order, ra_column, dec_column, use_hipscat_index
    ):
        mapped_pixel, count_at_pixel = np.unique(mapped_pixels, return_counts=True)
        mapped_pixel = mapped_pixel.astype(np.int64)
        histo[mapped_pixel] += count_at_pixel.astype(np.int64)
    ResumePlan.write_partial_histogram(tmp_path=resume_path, mapping_key=mapping_key, histogram=histo)


def split_pixels(
    input_file: FilePointer,
    file_reader: InputReader,
    splitting_key,
    highest_order,
    ra_column,
    dec_column,
    cache_shard_path: FilePointer,
    resume_path: FilePointer,
    alignment=None,
    use_hipscat_index=False,
):
    """Map a file of input objects to their healpix pixels and split into shards.

    Args:
        input_file (FilePointer): file to read for catalog data.
        file_reader (hipscat_import.catalog.file_readers.InputReader): instance
            of input reader that specifies arguments necessary for reading from the input file.
        splitting_key (str): unique counter for this input file, used
            when creating intermediate files
        highest_order (int): healpix order to use when mapping
        ra_column (str): where to find right ascension data in the dataframe
        dec_column (str): where to find declation in the dataframe
        cache_shard_path (FilePointer): where to write intermediate parquet files.
        resume_path (FilePointer): where to write resume files.

    Raises:
        ValueError: if the `ra_column` or `dec_column` cannot be found in the input file.
        FileNotFoundError: if the file does not exist, or is a directory
    """
    for chunk_number, data, mapped_pixels in _iterate_input_file(
        input_file, file_reader, highest_order, ra_column, dec_column, use_hipscat_index
    ):
        aligned_pixels = alignment[mapped_pixels]
        unique_pixels, unique_inverse = np.unique(aligned_pixels, return_inverse=True)

        for unique_index, [order, pixel, _] in enumerate(unique_pixels):
            mapped_indexes = np.where(unique_inverse == unique_index)
            data_indexes = data.index[mapped_indexes[0].tolist()]

            filtered_data = data.filter(items=data_indexes, axis=0)

            pixel_dir = _get_pixel_directory(cache_shard_path, order, pixel)
            file_io.make_directory(pixel_dir, exist_ok=True)
            output_file = file_io.append_paths_to_pointer(
                pixel_dir, f"shard_{splitting_key}_{chunk_number}.parquet"
            )
            if _has_named_index(filtered_data):
                filtered_data.to_parquet(output_file, index=True)
            else:
                filtered_data.to_parquet(output_file, index=False)
        del filtered_data, data_indexes

    ResumePlan.splitting_key_done(tmp_path=resume_path, splitting_key=splitting_key)


def reduce_pixel_shards(
    cache_shard_path,
    resume_path,
    reducing_key,
    destination_pixel_order,
    destination_pixel_number,
    destination_pixel_size,
    output_path,
    ra_column,
    dec_column,
    sort_columns: str = "",
    use_hipscat_index=False,
    add_hipscat_index=True,
    delete_input_files=True,
    use_schema_file="",
    storage_options: Union[Dict[Any, Any], None] = None,
):
    """Reduce sharded source pixels into destination pixels.

    In addition to combining multiple shards of data into a single
    parquet file, this method will add a few new columns:

        - ``Norder`` - the healpix order for the pixel
        - ``Dir`` - the directory part, corresponding to the pixel
        - ``Npix`` - the healpix pixel
        - ``_hipscat_index`` - optional - a spatially-correlated
          64-bit index field.

    Notes on ``_hipscat_index``:

        - if we generate the field, we will promote any previous
          *named* pandas index field(s) to a column with
          that name.
        - see ``hipscat.pixel_math.hipscat_id``
          for more in-depth discussion of this field.

    Args:
        cache_shard_path (FilePointer): where to read intermediate parquet files.
        resume_path (FilePointer): where to write resume files.
        reducing_key (str): unique string for this task, used for resume files.
        origin_pixel_numbers (list[int]): high order pixels, with object
            data written to intermediate directories.
        destination_pixel_order (int): order of the final catalog pixel
        destination_pixel_number (int): pixel number at the above order
        destination_pixel_size (int): expected number of rows to write
            for the catalog's final pixel
        output_path (FilePointer): where to write the final catalog pixel data
        sort_columns (str): column for survey identifier, or other sortable column
        add_hipscat_index (bool): should we add a _hipscat_index column to
            the resulting parquet file?
        delete_input_files (bool): should we delete the intermediate files
            used as input for this method.
        use_schema_file (str): use the parquet schema from the indicated
            parquet file.

    Raises:
        ValueError: if the number of rows written doesn't equal provided
            `destination_pixel_size`
    """
    destination_dir = paths.pixel_directory(output_path, destination_pixel_order, destination_pixel_number)
    file_io.make_directory(destination_dir, exist_ok=True, storage_options=storage_options)

    destination_file = paths.pixel_catalog_file(
        output_path, destination_pixel_order, destination_pixel_number
    )

    schema = None
    if use_schema_file:
        schema = file_io.read_parquet_metadata(
            use_schema_file, storage_options=storage_options
        ).schema.to_arrow_schema()

    tables = []
    pixel_dir = _get_pixel_directory(cache_shard_path, destination_pixel_order, destination_pixel_number)

    if schema:
        tables.append(pq.read_table(pixel_dir, schema=schema))
    else:
        tables.append(pq.read_table(pixel_dir))

    merged_table = pa.concat_tables(tables)

    rows_written = len(merged_table)

    if rows_written != destination_pixel_size:
        raise ValueError(
            "Unexpected number of objects at pixel "
            f"({destination_pixel_order}, {destination_pixel_number})."
            f" Expected {destination_pixel_size}, wrote {rows_written}"
        )

    dataframe = merged_table.to_pandas()
    if sort_columns:
        dataframe = dataframe.sort_values(sort_columns.split(","))
    if add_hipscat_index and not use_hipscat_index:
        dataframe[HIPSCAT_ID_COLUMN] = pixel_math.compute_hipscat_id(
            dataframe[ra_column].values,
            dataframe[dec_column].values,
        )

    dataframe["Norder"] = np.full(rows_written, fill_value=destination_pixel_order, dtype=np.uint8)
    dataframe["Dir"] = np.full(
        rows_written,
        fill_value=int(destination_pixel_number / 10_000) * 10_000,
        dtype=np.uint64,
    )
    dataframe["Npix"] = np.full(rows_written, fill_value=destination_pixel_number, dtype=np.uint64)

    if add_hipscat_index:
        ## If we had a meaningful index before, preserve it as a column.
        if _has_named_index(dataframe):
            dataframe = dataframe.reset_index()
        dataframe = dataframe.set_index(HIPSCAT_ID_COLUMN).sort_index()
    dataframe.to_parquet(destination_file, storage_options=storage_options)

    del dataframe, merged_table, tables

    if delete_input_files:
        pixel_dir = _get_pixel_directory(cache_shard_path, destination_pixel_order, destination_pixel_number)

        file_io.remove_directory(pixel_dir, ignore_errors=True, storage_options=storage_options)

    ResumePlan.reducing_key_done(tmp_path=resume_path, reducing_key=reducing_key)
