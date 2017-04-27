# coding=utf-8
# --------------------------------------------------------------------------
# Code generated by Microsoft (R) AutoRest Code Generator 1.0.1.0
# Changes may cause incorrect behavior and will be lost if the code is
# regenerated.
# --------------------------------------------------------------------------

from msrest.serialization import Model


class AccountPreferencesInternal(Model):
    """AccountPreferencesInternal.

    :param culture:
    :type culture: str
    :param language:
    :type language: str
    :param time_zone:
    :type time_zone: str
    """

    _attribute_map = {
        'culture': {'key': 'culture', 'type': 'str'},
        'language': {'key': 'language', 'type': 'str'},
        'time_zone': {'key': 'timeZone', 'type': 'str'},
    }

    def __init__(self, culture=None, language=None, time_zone=None):
        self.culture = culture
        self.language = language
        self.time_zone = time_zone
