# CI Utilities for Lifting Tools

This repository contains scripts and utilities for running binary lifting tools (like mcsema, anvill, remill, rellic, etc.) against various tests sets, like AnghaBench.

Doing a CI test is team effort; some code exists here and some in the individual tool repository.

## Cloud Scripts

The scripts in `cloud` create cloud instances for various cloud providers that launch a startup script and terminate the instance on script exit.


## Tool Run Scripts

These scripts run a tool (like Rellic, Anvill, etc.) on a large set of input files
