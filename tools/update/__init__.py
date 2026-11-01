"""The weekly dependency update (rulings plan#2351, plan#2448-plan#2450), each module run by a
job of ``.github/workflows/updates.yml``: the Monday lock refresh (``refresh``), by the job
that holds no secret and runs package code, writing the folder ``folder`` lays out; the
publish of that folder (``publish``) and the daily queue (``queue``), by the jobs that hold
the update App's token (``app``) and run main's own code and no package code but the
``landing`` group's, installed from main's lock, each file checked against its hash."""
