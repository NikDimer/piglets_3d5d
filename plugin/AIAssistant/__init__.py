# Copyright (c) 2026 piglets_3d5d
# AI Assistant plugin for STE Slicer — hackathon "Открой #Моспром" / Stereotech case.

from . import AIAssistant


def getMetaData():
    return {}


def register(app):
    return {"extension": AIAssistant.AIAssistant()}
