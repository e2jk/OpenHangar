#!/bin/bash -eu

for fuzzer in fuzz_url_validation fuzz_file_ext fuzz_numeric_inputs; do
  compile_python_fuzzer "$SRC/${fuzzer}.py"
done
