"""Data class to hold common runtime arguments for dataset creation."""

import re
from dataclasses import dataclass
from importlib.metadata import version

from hipscat.io import file_io

# pylint: disable=too-many-instance-attributes


@dataclass
class RuntimeArguments:
    """Data class for holding runtime arguments"""

    ## Output
    output_path: str = ""
    output_catalog_name: str = ""

    ## Execution
    tmp_dir: str = ""
    overwrite: bool = False
    progress_bar: bool = True
    dask_tmp: str = ""
    dask_n_workers: int = 1
    dask_threads_per_worker: int = 1

    catalog_path = ""
    tmp_path = ""

    def __post_init__(self):
        self._check_arguments()

    def _check_arguments(self):
        if not self.output_path:
            raise ValueError("output_path is required")
        if not self.output_catalog_name:
            raise ValueError("output_catalog_name is required")
        if re.search(r"[^A-Za-z0-9_\-\\]", self.output_catalog_name):
            raise ValueError("output_catalog_name contains invalid characters")

        if self.dask_n_workers <= 0:
            raise ValueError("dask_n_workers should be greather than 0")
        if self.dask_threads_per_worker <= 0:
            raise ValueError("dask_threads_per_worker should be greater than 0")

        if not file_io.does_file_or_directory_exist(self.output_path):
            raise FileNotFoundError(
                f"output_path ({self.output_path}) not found on local storage"
            )

        self.catalog_path = file_io.append_paths_to_pointer(
            self.output_path, self.output_catalog_name
        )
        if not self.overwrite:
            if file_io.directory_has_contents(self.catalog_path):
                raise ValueError(
                    f"output_path ({self.catalog_path}) contains files."
                    " choose a different directory or use --overwrite flag"
                )
        file_io.make_directory(self.catalog_path, exist_ok=True)

        if self.tmp_dir:
            if not file_io.does_file_or_directory_exist(self.tmp_dir):
                raise FileNotFoundError(
                    f"tmp_dir ({self.tmp_dir}) not found on local storage"
                )
            self.tmp_path = file_io.append_paths_to_pointer(
                self.tmp_dir, self.output_catalog_name, "intermediate"
            )
        elif self.dask_tmp:
            if not file_io.does_file_or_directory_exist(self.dask_tmp):
                raise FileNotFoundError(
                    f"dask_tmp ({self.dask_tmp}) not found on local storage"
                )
            self.tmp_path = file_io.append_paths_to_pointer(
                self.dask_tmp, self.output_catalog_name, "intermediate"
            )
        else:
            self.tmp_path = file_io.append_paths_to_pointer(
                self.catalog_path, "intermediate"
            )
        file_io.make_directory(self.tmp_path, exist_ok=True)

    def provenance_info(self) -> dict:
        """Fill all known information in a dictionary for provenance tracking.

        Returns:
            dictionary with all argument_name -> argument_value as key -> value pairs.
        """
        runtime_args = {
            "catalog_name": self.output_catalog_name,
            "output_path": str(self.output_path),
            "output_catalog_name": self.output_catalog_name,
            "tmp_dir": str(self.tmp_dir),
            "overwrite": self.overwrite,
            "dask_tmp": str(self.dask_tmp),
            "dask_n_workers": self.dask_n_workers,
            "dask_threads_per_worker": self.dask_threads_per_worker,
            "catalog_path": self.catalog_path,
            "tmp_path": str(self.tmp_path),
        }

        runtime_args.update(self.additional_runtime_provenance_info())
        provenance_info = {
            "tool_name": "hipscat_import",
            "version": version("hipscat-import"),
            "runtime_args": runtime_args,
        }

        return provenance_info

    def additional_runtime_provenance_info(self):
        """Any additional runtime args to be included in provenance info from subclasses"""
        return {}