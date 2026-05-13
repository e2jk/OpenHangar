FROM gcr.io/oss-fuzz-base/base-builder-python

RUN pip install atheris werkzeug

COPY . $SRC/openhangar
COPY fuzz/fuzz_*.py $SRC/

WORKDIR $SRC/openhangar
