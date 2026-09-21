# -*- coding: utf-8 -*-
"""What each bridge request does to the Revit model.

Every handler runs on Revit's UI thread inside an ExternalEvent (see
server.py) and takes (uiapp, params). Anything that modifies the document
does so inside one Transaction with a failure preprocessor that swallows
warnings and rolls back on errors, so an unattended session never blocks
on a "wall slightly off axis" dialog.
"""
from __future__ import print_function

import sys
import traceback

import clr

clr.AddReference("System")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from System.Collections.Generic import List  # noqa: E402

from Autodesk.Revit import DB  # noqa: E402
from Autodesk.Revit import UI  # noqa: E402

from . import BRIDGE_VERSION  # noqa: E402
from .protocol import PROTOCOL_VERSION, CommandError  # noqa: E402
from .serialize import (  # noqa: E402
    element_details,
    element_id_value,
    element_summary,
    parameter_info,
    safe_name,
    to_jsonable,
    xyz,
)

try:
    basestring
except NameError:  # pragma: no cover
    basestring = str  # noqa: A001

try:
    from StringIO import StringIO
except ImportError:  # pragma: no cover
    from io import StringIO

COMMANDS = {}


def command(name):
    def register(fn):
        COMMANDS[name] = fn
        return fn

    return register


def dispatch(uiapp, method, params):
    handler = COMMANDS.get(method)
    if handler is None:
        raise CommandError("unknown_method", "no such command: %s (known: %s)" % (method, ", ".join(sorted(COMMANDS))))
    return handler(uiapp, params)


# ----------------------------------------------------------------------------- helpers


def require_doc(uiapp):
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None or uidoc.Document is None:
        raise CommandError("no_document", "no project is open in Revit; open a model first")
    return uidoc.Document, uidoc


def need(params, name, kind=None):
    if name not in params or params[name] is None:
        raise CommandError("invalid_params", "missing required parameter '%s'" % name)
    value = params[name]
    if kind is not None:
        try:
            return kind(value)
        except (TypeError, ValueError):
            raise CommandError("invalid_params", "parameter '%s' must be %s, got %r" % (name, kind.__name__, value))
    return value


def opt(params, name, default=None, kind=None):
    value = params.get(name)
    if value is None:
        return default
    if kind is not None:
        try:
            return kind(value)
        except (TypeError, ValueError):
            raise CommandError("invalid_params", "parameter '%s' must be %s, got %r" % (name, kind.__name__, value))
    return value


def point(params, name):
    raw = need(params, name)
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise CommandError("invalid_params", "'%s' must be [x, y, z] in feet" % name)
    try:
        return DB.XYZ(float(raw[0]), float(raw[1]), float(raw[2]))
    except (TypeError, ValueError):
        raise CommandError("invalid_params", "'%s' must contain three numbers" % name)


def get_element(doc, element_id, what="element", cls=None):
    try:
        eid = DB.ElementId(int(element_id))
    except (TypeError, ValueError):
        raise CommandError("invalid_params", "%s id must be an integer, got %r" % (what, element_id))
    element = doc.GetElement(eid)
    if element is None:
        raise CommandError("not_found", "no %s with id %s in %s" % (what, element_id, doc.Title))
    if cls is not None and not isinstance(element, cls):
        raise CommandError(
            "invalid_params",
            "element %s is a %s, not a %s" % (element_id, type(element).__name__, cls.__name__),
        )
    return element


class _FailureSwallower(DB.IFailuresPreprocessor):
    """Delete warnings, roll back on errors, and remember what Revit complained about."""

    def __init__(self):
        self.messages = []

    def PreprocessFailures(self, accessor):
        result = DB.FailureProcessingResult.Continue
        for failure in accessor.GetFailureMessages():
            text = failure.GetDescriptionText()
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                self.messages.append("warning (suppressed): %s" % text)
                accessor.DeleteWarning(failure)
            else:
                self.messages.append("error: %s" % text)
                result = DB.FailureProcessingResult.ProceedWithRollBack
        return result


class transaction(object):
    """`with transaction(doc, name) as t:` commits on success, rolls back on any exception."""

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.swallower = _FailureSwallower()
        self.t = None

    def __enter__(self):
        self.t = DB.Transaction(self.doc, self.name)
        options = self.t.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(self.swallower)
        options.SetClearAfterRollback(True)
        self.t.SetFailureHandlingOptions(options)
        self.t.Start()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            if self.t.HasStarted() and not self.t.HasEnded():
                self.t.RollBack()
            return False
        if self.t.HasEnded():
            # A regeneration inside the body already processed failures and rolled back.
            raise CommandError(
                "revit_error",
                "Revit rolled back '%s' during regeneration: %s" % (self.name, "; ".join(self.swallower.messages) or "no details"),
            )
        status = self.t.Commit()
        if status != DB.TransactionStatus.Committed:
            raise CommandError(
                "revit_error",
                "Revit rolled back '%s' (%s): %s" % (self.name, status, "; ".join(self.swallower.messages) or "no details"),
            )
        return False


# ----------------------------------------------------------------------------- session


def _document_info(doc):
    if doc is None:
        return None
    return {
        "title": doc.Title,
        "path": doc.PathName or None,
        "is_family": doc.IsFamilyDocument,
        "is_workshared": doc.IsWorkshared,
        "is_modified": doc.IsModified,
    }


def _pyrevit_version():
    try:
        from pyrevit import versionmgr

        return versionmgr.get_pyrevit_version().get_formatted()
    except Exception:
        return None


@command("hello")
def hello(uiapp, params):
    app = uiapp.Application
    uidoc = uiapp.ActiveUIDocument
    return {
        "bridge_version": BRIDGE_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "engine": "IronPython %s" % ".".join(str(v) for v in sys.version_info[:3]),
        "pyrevit_version": _pyrevit_version(),
        "revit": {
            "version_number": app.VersionNumber,
            "version_name": app.VersionName,
            "version_build": app.VersionBuild,
            "username": app.Username,
        },
        "document": _document_info(uidoc.Document if uidoc is not None else None),
        "open_documents": [d.Title for d in app.Documents],
    }


@command("get_project_info")
def get_project_info(uiapp, params):
    doc, _ = require_doc(uiapp)
    pi = doc.ProjectInformation
    fields = {}
    for name in (
        "Name",
        "Number",
        "ClientName",
        "Address",
        "Status",
        "BuildingName",
        "Author",
        "OrganizationName",
        "OrganizationDescription",
        "IssueDate",
    ):
        try:
            fields[name] = getattr(pi, name)
        except Exception:
            fields[name] = None
    length_unit = None
    try:
        unit_id = doc.GetUnits().GetFormatOptions(DB.SpecTypeId.Length).GetUnitTypeId()
        length_unit = DB.LabelUtils.GetLabelForUnit(unit_id)
    except Exception:
        pass
    info = _document_info(doc)
    info["project_information_id"] = element_id_value(pi.Id)
    info["project_information"] = fields
    info["length_display_unit"] = length_unit
    info["internal_length_unit"] = "feet"
    return info


@command("list_levels")
def list_levels(uiapp, params):
    doc, _ = require_doc(uiapp)
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    levels.sort(key=lambda lv: lv.Elevation)
    return {
        "levels": [{"id": element_id_value(lv.Id), "name": lv.Name, "elevation": lv.Elevation} for lv in levels]
    }


# ----------------------------------------------------------------------------- categories & elements


def _model_and_annotation_categories(doc):
    out = []
    for cat in doc.Settings.Categories:
        try:
            if cat.CategoryType not in (DB.CategoryType.Model, DB.CategoryType.Annotation):
                continue
            bic = cat.BuiltInCategory
            if bic == DB.BuiltInCategory.INVALID:
                continue
        except Exception:
            continue
        out.append(cat)
    return out


def resolve_category(doc, name):
    """Accept 'Walls', 'walls', 'OST_Walls' or a localized category name. Returns a Category."""
    if not isinstance(name, basestring) or not name.strip():
        raise CommandError("invalid_params", "category must be a non-empty string")
    wanted = name.strip().lower()
    cats = _model_and_annotation_categories(doc)
    for cat in cats:
        if cat.Name.lower() == wanted:
            return cat
    for cat in cats:
        if str(cat.BuiltInCategory).lower() in (wanted, "ost_" + wanted.replace(" ", "")):
            return cat
    raise CommandError(
        "not_found",
        "no category named %r; use list_categories to see the names this model accepts" % name,
    )


@command("list_categories")
def list_categories(uiapp, params):
    doc, _ = require_doc(uiapp)
    with_counts = bool(opt(params, "with_counts", False))
    rows = []
    for cat in _model_and_annotation_categories(doc):
        row = {
            "name": cat.Name,
            "built_in": str(cat.BuiltInCategory),
            "category_type": str(cat.CategoryType),
            "id": element_id_value(cat.Id),
        }
        if with_counts:
            row["instance_count"] = (
                DB.FilteredElementCollector(doc).OfCategoryId(cat.Id).WhereElementIsNotElementType().GetElementCount()
            )
        rows.append(row)
    rows.sort(key=lambda r: r["name"].lower())
    if with_counts:
        rows = [r for r in rows if r["instance_count"] > 0]
    return {"categories": rows, "count": len(rows)}


@command("list_elements")
def list_elements(uiapp, params):
    doc, _ = require_doc(uiapp)
    cat = resolve_category(doc, need(params, "category"))
    limit = max(0, opt(params, "limit", 100, int))
    offset = max(0, opt(params, "offset", 0, int))
    element_types = bool(opt(params, "element_types", False))
    collector = DB.FilteredElementCollector(doc).OfCategoryId(cat.Id)
    if element_types:
        collector = collector.WhereElementIsElementType()
    else:
        collector = collector.WhereElementIsNotElementType()
    all_ids = sorted(collector.ToElementIds(), key=element_id_value)  # stable paging across calls
    page = all_ids[offset : offset + limit]
    return {
        "category": cat.Name,
        "built_in_category": str(cat.BuiltInCategory),
        "element_types": element_types,
        "total": len(all_ids),
        "offset": offset,
        "limit": limit,
        "elements": [element_summary(doc, doc.GetElement(eid)) for eid in page],
    }


@command("get_element")
def get_element_cmd(uiapp, params):
    doc, _ = require_doc(uiapp)
    element = get_element(doc, need(params, "element_id"))
    return element_details(doc, element, bool(opt(params, "include_type_parameters", False)))


def _coerce_and_set(param, value):
    """Set a parameter from a JSON value. Returns False when Revit rejects the value."""
    storage = param.StorageType
    if storage == DB.StorageType.String:
        return param.Set(u"%s" % value if not isinstance(value, basestring) else value)
    if storage == DB.StorageType.Double:
        if isinstance(value, basestring):
            return param.SetValueString(value)
        return param.Set(float(value))
    if storage == DB.StorageType.Integer:
        if isinstance(value, bool):
            return param.Set(1 if value else 0)
        if isinstance(value, basestring):
            try:
                return param.Set(int(value))
            except ValueError:
                return param.SetValueString(value)
        return param.Set(int(value))
    if storage == DB.StorageType.ElementId:
        return param.Set(DB.ElementId(int(value)))
    raise CommandError("invalid_params", "parameter has no storage type and cannot be set")


@command("set_parameter")
def set_parameter(uiapp, params):
    doc, _ = require_doc(uiapp)
    element = get_element(doc, need(params, "element_id"))
    name = need(params, "parameter_name")
    if "value" not in params:
        raise CommandError("invalid_params", "missing required parameter 'value'")
    value = params["value"]
    param = element.LookupParameter(name)
    if param is None:
        names = sorted(set(p.Definition.Name for p in element.Parameters))
        raise CommandError(
            "not_found",
            "element %s has no parameter named %r" % (element_id_value(element.Id), name),
            "available: " + ", ".join(names),
        )
    if param.IsReadOnly:
        raise CommandError("invalid_params", "parameter %r is read-only on element %s" % (name, element_id_value(element.Id)))
    before = parameter_info(param)
    with transaction(doc, "RevitMCP: set %s" % name):
        try:
            accepted = _coerce_and_set(param, value)
        except (TypeError, ValueError) as exc:
            raise CommandError(
                "invalid_params", "cannot store %r in a %s parameter: %s" % (value, param.StorageType, exc)
            )
        if not accepted:
            raise CommandError(
                "revit_error",
                "Revit rejected %r for parameter %r (storage type %s)" % (value, name, param.StorageType),
            )
    return {
        "element_id": element_id_value(element.Id),
        "parameter": name,
        "before": before,
        "after": parameter_info(param),
    }


@command("delete_elements")
def delete_elements(uiapp, params):
    doc, _ = require_doc(uiapp)
    raw_ids = need(params, "element_ids")
    if not isinstance(raw_ids, (list, tuple)) or not raw_ids:
        raise CommandError("invalid_params", "element_ids must be a non-empty list of integers")
    ids = List[DB.ElementId]()
    for raw in raw_ids:
        ids.Add(get_element(doc, raw).Id)
    with transaction(doc, "RevitMCP: delete %d element(s)" % len(raw_ids)):
        deleted = doc.Delete(ids)
    return {"requested_ids": [int(i) for i in raw_ids], "deleted_ids": sorted(element_id_value(i) for i in deleted)}


# ----------------------------------------------------------------------------- creation


@command("create_wall")
def create_wall(uiapp, params):
    doc, _ = require_doc(uiapp)
    start = point(params, "start")
    end = point(params, "end")
    level = get_element(doc, need(params, "level_id"), "level", DB.Level)
    height = opt(params, "height", 10.0, float)
    structural = bool(opt(params, "structural", False))
    wall_type_id = opt(params, "wall_type_id")
    if wall_type_id is None:
        type_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.WallType)
    else:
        type_id = get_element(doc, wall_type_id, "wall type", DB.WallType).Id
    if height <= 0:
        raise CommandError("invalid_params", "height must be positive (feet)")
    if start.DistanceTo(end) < doc.Application.ShortCurveTolerance:
        raise CommandError("invalid_params", "start and end are too close together to make a wall")
    line = DB.Line.CreateBound(start, end)
    with transaction(doc, "RevitMCP: create wall"):
        wall = DB.Wall.Create(doc, line, type_id, level.Id, height, 0.0, False, structural)
    # The wall exists from here on; nothing below may fail in a way that loses its id.
    info = {"id": element_id_value(wall.Id)}
    try:
        info.update(element_summary(doc, wall))
        info["location"] = {"start": xyz(line.GetEndPoint(0)), "end": xyz(line.GetEndPoint(1)), "length": line.Length}
        height_param = wall.get_Parameter(DB.BuiltInParameter.WALL_USER_HEIGHT_PARAM)
        info["height"] = height_param.AsDouble() if height_param is not None else None
    except Exception as exc:
        info["warning"] = "wall created but describing it failed: %s" % exc
    return info


@command("create_level")
def create_level(uiapp, params):
    doc, _ = require_doc(uiapp)
    name = need(params, "name")
    elevation = need(params, "elevation", float)
    for existing in DB.FilteredElementCollector(doc).OfClass(DB.Level):
        if existing.Name == name:
            raise CommandError("invalid_params", "a level named %r already exists (id %s)" % (name, element_id_value(existing.Id)))
    with transaction(doc, "RevitMCP: create level %s" % name):
        level = DB.Level.Create(doc, elevation)
        level.Name = name
    return {"id": element_id_value(level.Id), "name": level.Name, "elevation": level.Elevation}


@command("create_sheet")
def create_sheet(uiapp, params):
    doc, _ = require_doc(uiapp)
    number = need(params, "sheet_number")
    name = need(params, "sheet_name")
    tb = opt(params, "titleblock_type_id")
    tb_id = DB.ElementId.InvalidElementId
    if tb is not None:
        tb_id = get_element(doc, tb, "title block type", DB.FamilySymbol).Id
    for existing in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        if existing.SheetNumber == number:
            raise CommandError("invalid_params", "sheet number %r is already used (id %s)" % (number, element_id_value(existing.Id)))
    with transaction(doc, "RevitMCP: create sheet %s" % number):
        sheet = DB.ViewSheet.Create(doc, tb_id)
        sheet.SheetNumber = number
        sheet.Name = name
    return {
        "id": element_id_value(sheet.Id),
        "sheet_number": sheet.SheetNumber,
        "name": sheet.Name,
        "titleblock_type_id": element_id_value(tb_id) if tb is not None else None,
    }


# ----------------------------------------------------------------------------- views & sheets

_HIDDEN_VIEW_TYPES = ("Internal", "ProjectBrowser", "SystemBrowser", "Undefined", "DrawingSheet")


@command("list_views")
def list_views(uiapp, params):
    doc, _ = require_doc(uiapp)
    wanted = opt(params, "view_type")
    include_templates = bool(opt(params, "include_templates", False))
    rows = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        vtype = str(view.ViewType)
        if vtype in _HIDDEN_VIEW_TYPES:
            continue
        if view.IsTemplate and not include_templates:
            continue
        if wanted and vtype.lower() != str(wanted).lower():
            continue
        try:
            level = view.GenLevel
        except Exception:
            level = None
        try:
            scale = view.Scale
        except Exception:
            scale = None
        rows.append(
            {
                "id": element_id_value(view.Id),
                "name": view.Name,
                "title": view.Title,
                "view_type": vtype,
                "scale": scale,
                "is_template": view.IsTemplate,
                "level": level.Name if level is not None else None,
                "level_id": element_id_value(level.Id) if level is not None else None,
            }
        )
    rows.sort(key=lambda r: (r["view_type"], r["name"].lower()))
    return {"views": rows, "count": len(rows)}


@command("list_sheets")
def list_sheets(uiapp, params):
    doc, _ = require_doc(uiapp)
    rows = []
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        rows.append(
            {
                "id": element_id_value(sheet.Id),
                "sheet_number": sheet.SheetNumber,
                "name": sheet.Name,
                "is_placeholder": sheet.IsPlaceholder,
                "placed_view_ids": sorted(element_id_value(v) for v in sheet.GetAllPlacedViews()),
            }
        )
    rows.sort(key=lambda r: r["sheet_number"])
    return {"sheets": rows, "count": len(rows)}


# ----------------------------------------------------------------------------- escape hatch


@command("execute_python")
def execute_python(uiapp, params):
    code = need(params, "code")
    if not isinstance(code, basestring):
        raise CommandError("invalid_params", "code must be a string")
    use_transaction = bool(opt(params, "transaction", True))
    uidoc = uiapp.ActiveUIDocument
    doc = uidoc.Document if uidoc is not None else None
    if use_transaction and doc is None:
        raise CommandError("no_document", "no project is open; open a model or pass transaction=false")
    try:
        # dont_inherit: this module's `from __future__ import print_function` must not
        # leak into user code, which may legitimately use the 2.7 print statement.
        compiled = compile(code, "<execute_python>", "exec", 0, True)
    except SyntaxError as exc:
        raise CommandError("invalid_params", "syntax error in code: %s" % exc, traceback.format_exc())
    namespace = {
        "__name__": "__revit_mcp__",
        "doc": doc,
        "uidoc": uidoc,
        "app": uiapp.Application,
        "uiapp": uiapp,
        "DB": DB,
        "UI": UI,
        "clr": clr,
        "FilteredElementCollector": DB.FilteredElementCollector,
        "Transaction": DB.Transaction,
        "ElementId": DB.ElementId,
        "XYZ": DB.XYZ,
        "BuiltInCategory": DB.BuiltInCategory,
        "BuiltInParameter": DB.BuiltInParameter,
        "result": None,
    }
    buffer = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buffer
    try:
        if use_transaction:
            with transaction(doc, "RevitMCP: execute_python"):
                exec(compiled, namespace)
        else:
            exec(compiled, namespace)
    except CommandError:
        raise
    except Exception as exc:
        raise CommandError(
            "revit_error",
            "%s: %s" % (type(exc).__name__, exc),
            traceback.format_exc() + "\n--- stdout ---\n" + _captured(buffer),
        )
    finally:
        sys.stdout = old_stdout
    return {"result": to_jsonable(namespace.get("result")), "stdout": _captured(buffer)}


def _captured(buffer):
    try:
        return buffer.getvalue()
    except UnicodeError:
        return "<stdout mixed str and unicode; could not decode>"
