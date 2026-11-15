"""Asking an OpenAI-compatible server for the settings that match a song.

``store`` keeps the server settings, ``client`` talks to the server, ``prompt``
builds the request, ``parse`` turns the reply into a :class:`~katatone.ai.proposal.Proposal`
(what could be changed in the patch, row by row) and ``spec`` ties the reply's keys to the
amp's parameters.
"""
