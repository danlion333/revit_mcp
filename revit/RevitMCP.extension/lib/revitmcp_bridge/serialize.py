# -*- coding: utf-8 -*-
"""Turn Revit API objects into plain JSON-able Python values."""
from __future__ import print_function

import clr

from Autodesk.Revit import DB

clr.AddReference("RevitAPI")
clr.AddReference("System")
from System import Int32, Int64  # noqa: E402

try:
    basestring
except NameError:  # pragma: no cover - IronPython 3
    basestring = str  # noqa: A001
try:
    long
except NameError:  # pragma: no cover - IronPython 3
    long = int  # noqa: A001


def element_id_value(element_id):
    """Integer value of an ElementId across Revit versions (Value since 2024, IntegerValue before)."""
    if element_id is None:
        return None
    value = getattr(element_id, "Value", None)
    if value is None:
        value = element_id.IntegerValue
    return int(value)


def make_element_id(value):
    """ElementId from a Python int.

    Revit 2024+ has ElementId(Int64) next to the deprecated ElementId(Int32) and the
    enum constructors; handed a bare Python int, IronPython's overload resolution
    reports the call as ambiguous, so pick the overload explicitly.
    """
    number = int(value)
    try:
        return DB.ElementId(Int64(number))
    except TypeError:  # Revit 2023 and older: no Int64 overload
        return DB.ElementId(Int32(number))


def xyz(point):
    return [point.X, point.Y, point.Z]


def to_jsonable(value, depth=0):
    """Best-effort conversion for execute_python results and parameter values."""
    if value is None or isinstance(value, (bool, int, long, float, basestring)):
        return value
    if isinstance(value, DB.ElementId):
        return element_id_value(value)
    if isinstance(value, DB.XYZ):
        return xyz(value)
    if isinstance(value, DB.Element):
        return element_summary(value.Document, value)
    if depth >= 6:
        return str(value)
    if isinstance(value, dict):
        return dict((str(k), to_jsonable(v, depth + 1)) for k, v in value.items())
    if hasattr(value, "__iter__"):
        try:
            return [to_jsonable(v, depth + 1) for v in value]
        except Exception:
            pass
    try:
        return str(value)
    except Exception:
        return repr(value)


def safe_name(element):
    try:
        return element.Name
    except Exception:
        pass
    # ElementType (and a few others) redeclare Name, and IronPython reports the
    # lookup as ambiguous; the base property getter still works.
    try:
        return DB.Element.Name.__get__(element)
    except Exception:
        return None


def category_info(element):
    cat = element.Category
    if cat is None:
        return None, None
    built_in = None
    try:
        bic = cat.BuiltInCategory
        if bic != DB.BuiltInCategory.INVALID:
            built_in = str(bic)
    except Exception:
        pass
    return cat.Name, built_in


def type_info(doc, element):
    """(family_name, type_name, type_id) for an instance, or (family_name, name, id) for a type."""
    if isinstance(element, DB.ElementType):
        return getattr(element, "FamilyName", None), safe_name(element), element_id_value(element.Id)
    type_id = element.GetTypeId()
    if type_id is None or type_id == DB.ElementId.InvalidElementId:
        return None, None, None
    etype = doc.GetElement(type_id)
    if etype is None:
        return None, None, element_id_value(type_id)
    return getattr(etype, "FamilyName", None), safe_name(etype), element_id_value(type_id)


def level_info(doc, element):
    try:
        level_id = element.LevelId
    except Exception:
        return None, None
    if level_id is None or level_id == DB.ElementId.InvalidElementId:
        return None, None
    level = doc.GetElement(level_id)
    return (safe_name(level) if level is not None else None), element_id_value(level_id)


def element_summary(doc, element):
    cat_name, built_in = category_info(element)
    family, type_name, type_id = type_info(doc, element)
    level_name, level_id = level_info(doc, element)
    return {
        "id": element_id_value(element.Id),
        "name": safe_name(element),
        "category": cat_name,
        "built_in_category": built_in,
        "family": family,
        "type": type_name,
        "type_id": type_id,
        "level": level_name,
        "level_id": level_id,
        "is_element_type": isinstance(element, DB.ElementType),
    }


def location_info(element):
    loc = element.Location
    if loc is None:
        return None
    if isinstance(loc, DB.LocationPoint):
        info = {"kind": "point", "point": xyz(loc.Point)}
        try:
            info["rotation"] = loc.Rotation
        except Exception:
            pass
        return info
    if isinstance(loc, DB.LocationCurve):
        curve = loc.Curve
        info = {
            "kind": "curve",
            "curve_type": type(curve).__name__,
            "start": xyz(curve.GetEndPoint(0)),
            "end": xyz(curve.GetEndPoint(1)),
            "length": curve.Length,
        }
        return info
    return {"kind": type(loc).__name__}


def bounding_box_info(element):
    try:
        box = element.get_BoundingBox(None)
    except Exception:
        return None
    if box is None:
        return None
    return {"min": xyz(box.Min), "max": xyz(box.Max)}


def parameter_value(param):
    storage = param.StorageType
    if not param.HasValue:
        return None
    if storage == DB.StorageType.Double:
        return param.AsDouble()
    if storage == DB.StorageType.Integer:
        return param.AsInteger()
    if storage == DB.StorageType.String:
        return param.AsString()
    if storage == DB.StorageType.ElementId:
        return element_id_value(param.AsElementId())
    return None


def parameter_info(param):
    definition = param.Definition
    built_in = None
    try:
        bip = definition.BuiltInParameter
        if bip != DB.BuiltInParameter.INVALID:
            built_in = str(bip)
    except Exception:
        pass
    try:
        value_string = param.AsValueString()
    except Exception:
        value_string = None
    return {
        "name": definition.Name,
        "built_in": built_in,
        "storage_type": str(param.StorageType),
        "value": parameter_value(param),
        "value_string": value_string,
        "is_read_only": param.IsReadOnly,
        "is_shared": param.IsShared,
        "has_value": param.HasValue,
    }


def parameters_of(element):
    params = [parameter_info(p) for p in element.Parameters]
    params.sort(key=lambda p: p["name"].lower())
    return params


def element_details(doc, element, include_type_parameters=False):
    info = element_summary(doc, element)
    info["unique_id"] = element.UniqueId
    info["location"] = location_info(element)
    info["bounding_box"] = bounding_box_info(element)
    info["parameters"] = parameters_of(element)
    if include_type_parameters and not isinstance(element, DB.ElementType):
        type_id = element.GetTypeId()
        etype = doc.GetElement(type_id) if type_id != DB.ElementId.InvalidElementId else None
        info["type_parameters"] = parameters_of(etype) if etype is not None else []
    return info
