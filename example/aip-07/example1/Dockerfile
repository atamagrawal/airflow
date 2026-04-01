# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

# This is an example Dockerfile for local testing. It is not intended for production use.
#
# Option B: extend the official Airflow image with the in-repo **data contracts** provider
# (``apache-airflow-providers-data-contracts``), which is not on PyPI while the provider is incubating.
#
# Build from the **repository root** (the directory that contains ``providers/``):
#
#   docker build -f example/aip-07/example1/Dockerfile -t airflow-aip07-data-contracts:latest .
#
# Tag and push to Docker Hub (repository ``atamagrawal/airflow``; pick your own tag):
#
#   docker tag airflow-aip07-data-contracts:latest atamagrawal/airflow:aip07-data-contracts-3.1.8
#   docker push atamagrawal/airflow:aip07-data-contracts-3.1.8
#
# Point your docker-compose quick-start at this image, for example in ``.env``:
#
#   AIRFLOW_IMAGE_NAME=atamagrawal/airflow:aip07-data-contracts-3.1.8
#
# Or a locally built tag only:
#
#   AIRFLOW_IMAGE_NAME=airflow-aip07-data-contracts:latest
#
# Or uncomment ``build: .`` next to your compose file and set ``context`` to the repo root with this
# file as ``dockerfile`` (see https://airflow.apache.org/docs/docker-stack/build.html ).
#
# ``AIRFLOW_IMAGE_TAG`` — ``apache/airflow`` image tag (e.g. ``3.1.8`` or ``3.1.8-python3.12``).
# ``AIRFLOW_VERSION`` — semver for ``pip install apache-airflow==...`` (use ``X.Y.Z`` only, no suffix).
# Override at build time: ``docker build --build-arg AIRFLOW_VERSION=3.1.8 --build-arg AIRFLOW_IMAGE_TAG=3.1.8-python3.12 ...``

ARG AIRFLOW_IMAGE_TAG=3.1.8
ARG AIRFLOW_VERSION=3.1.8

FROM apache/airflow:${AIRFLOW_IMAGE_TAG}

ARG AIRFLOW_VERSION
ENV AIRFLOW_VERSION=${AIRFLOW_VERSION}

USER root

COPY providers/data/contracts /tmp/apache-airflow-providers-data-contracts

# COPY is root-owned; the airflow user cannot ``rm`` that tree afterward unless we chown first.
RUN chown -R airflow:root /tmp/apache-airflow-providers-data-contracts

USER airflow

RUN pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    /tmp/apache-airflow-providers-data-contracts \
    && rm -rf /tmp/apache-airflow-providers-data-contracts
