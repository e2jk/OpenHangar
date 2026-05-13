FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:52b32769a56b9fe3350908266099d78852626ea44664ffdc7c12f43f62abb3f8

RUN pip install atheris==3.0.0 werkzeug==3.1.8

COPY .clusterfuzzlite/fuzz_url_validation.py \
     .clusterfuzzlite/fuzz_file_ext.py \
     .clusterfuzzlite/fuzz_numeric_inputs.py \
     $SRC/
COPY .clusterfuzzlite/build.sh $SRC/
