# RevitMCP Addon for Autodesk Revit
# 
# This addon creates a socket server within Revit to allow
# Claude AI to interact with Revit through the Model Context Protocol (MCP).

# Common Imports
import json
import threading
import socket
import time
import traceback
import sys
import os
import tempfile
from threading import Timer

# Dictionary to store registered commands
commands = {}

# Flag for PolyHaven integration (would need to be implemented for Revit if desired)
use_external_assets = False

# Socket server class
class RevitMCPServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.client = None
        self.buffer = b''  # Buffer for incomplete data
        self.timer = None
    
    def start(self):
        """Start the RevitMCP server"""
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.settimeout(0.1)  # Small timeout for non-blocking operation
            print(f"RevitMCP server started on {self.host}:{self.port}")
            
            # Start the server processing in a background thread
            self.server_thread = threading.Thread(target=self._process_server)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            return True
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()
            return False
            
    def stop(self):
        """Stop the RevitMCP server"""
        self.running = False
        if self.socket:
            self.socket.close()
        if self.client:
            self.client.close()
        self.socket = None
        self.client = None
        print("RevitMCP server stopped")
        
        # Wait for server thread to terminate
        if hasattr(self, 'server_thread') and self.server_thread:
            self.server_thread.join(timeout=1.0)

    def _process_server(self):
        """Background thread to process server operations"""
        while self.running:
            try:
                # Accept new connections
                if not self.client and self.socket:
                    try:
                        self.client, address = self.socket.accept()
                        self.client.settimeout(0.1)  # Small timeout for non-blocking
                        print(f"Connected to client: {address}")
                    except socket.timeout:
                        pass  # No connection waiting
                    except Exception as e:
                        print(f"Error accepting connection: {str(e)}")
                    
                # Process existing connection
                if self.client:
                    try:
                        # Try to receive data
                        try:
                            data = self.client.recv(8192)
                            if data:
                                self.buffer += data
                                # Try to process complete messages
                                try:
                                    # Attempt to parse the buffer as JSON
                                    command = json.loads(self.buffer.decode('utf-8'))
                                    # If successful, clear the buffer and process command
                                    self.buffer = b''
                                    response = self.execute_command(command)
                                    response_json = json.dumps(response)
                                    self.client.sendall(response_json.encode('utf-8'))
                                except json.JSONDecodeError:
                                    # Incomplete data, keep in buffer
                                    pass
                            else:
                                # Connection closed by client
                                print("Client disconnected")
                                self.client.close()
                                self.client = None
                        except socket.timeout:
                            # No data waiting, continue loop
                            pass
                        except Exception as e:
                            print(f"Error receiving data: {str(e)}")
                            self.client.close()
                            self.client = None
                    except Exception as e:
                        print(f"Error processing client connection: {str(e)}")
                        if self.client:
                            self.client.close()
                            self.client = None
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
            
            # Small sleep to prevent CPU hogging
            time.sleep(0.01)
    
    def execute_command(self, command):
        """Execute a command from the client"""
        try:
            command_type = command.get("type", "")
            params = command.get("params", {})
            
            print(f"Executing command: {command_type} with params: {params}")
            
            # Execute the command with Revit API access
            return self._execute_command_internal(command)
        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e)
            }
    
    def _execute_command_internal(self, command):
        """Execute command with access to the Revit API"""
        try:
            command_type = command.get("type", "")
            params = command.get("params", {})
            
            # Check if the command is registered
            if command_type in commands:
                command_func = commands[command_type]
                # Try to call the function with appropriate parameters
                try:
                    result = command_func(self, **params)
                    return {
                        "status": "success",
                        "result": result
                    }
                except Exception as e:
                    print(f"Error in command {command_type}: {str(e)}")
                    traceback.print_exc()
                    return {
                        "status": "error",
                        "message": f"Error executing {command_type}: {str(e)}"
                    }
            else:
                print(f"Unknown command: {command_type}")
                return {
                    "status": "error",
                    "message": f"Unknown command: {command_type}"
                }
        except Exception as e:
            print(f"Internal command execution error: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e)
            }

# ======================================================================
# Command registration decorator and implementation
# ======================================================================

def register_command(command_name):
    """Decorator to register a function as a command handler"""
    def decorator(func):
        commands[command_name] = func
        return func
    return decorator


# ======================================================================
# Command Implementations
# ======================================================================

@register_command("get_project_info")
def get_project_info(server):
    """Get information about the current Revit project"""
    # This would use the Revit API to get project information
    # For example:
    # from Autodesk.Revit.DB import Document
    # doc = __revit__.ActiveUIDocument.Document
    # project_info = doc.ProjectInformation
    
    # For demonstration, return dummy data
    return {
        "project_name": "Sample Revit Project",
        "project_number": "2023-001",
        "client_name": "Sample Client",
        "project_address": "123 Main St, Anytown, USA",
        "project_status": "Design Development",
        "revit_version": "2023",
        "last_saved": "2023-05-15T14:30:00",
        "file_size": "25.4 MB",
        "levels": ["Level 1", "Level 2", "Roof"],
        "view_count": 15,
        "element_count": 1243
    }


@register_command("get_element_info")
def get_element_info(server, element_id):
    """Get detailed information about a specific Revit element"""
    # This would use the Revit API to get element information
    # For example:
    # from Autodesk.Revit.DB import ElementId, Document
    # doc = __revit__.ActiveUIDocument.Document
    # element = doc.GetElement(ElementId(int(element_id)))
    
    # For demonstration, return dummy data
    if element_id == "123456":
        return {
            "id": element_id,
            "category": "Walls",
            "family": "Basic Wall",
            "type": "Generic - 8\"",
            "level": "Level 1",
            "dimensions": {
                "length": 20.0,
                "height": 10.0,
                "width": 0.66
            },
            "location": [10.0, 15.0, 0.0],
            "parameters": {
                "Fire Rating": "1 Hour",
                "Cost": 500.00,
                "Comments": "North exterior wall"
            }
        }
    else:
        return {
            "id": element_id,
            "category": "Unknown Element",
            "message": f"Element with ID {element_id} not found"
        }


@register_command("create_element")
def create_element(server, element_type, family_name=None, type_name=None, 
                   parameters=None, location=None, level_name=None):
    """Create a new element in the Revit project"""
    # This would use the Revit API to create a new element
    # For example:
    # from Autodesk.Revit.DB import Wall, Line, XYZ, ElementId
    # doc = __revit__.ActiveUIDocument.Document
    # transaction = Transaction(doc, "Create Element")
    # transaction.Start()
    # ... create element logic here ...
    # transaction.Commit()
    
    # For demonstration, return dummy data
    new_id = "987654" 
    return {
        "result": "success",
        "message": f"Created {element_type} element",
        "element": {
            "id": new_id,
            "type": element_type,
            "family": family_name or "Default Family",
            "type_name": type_name or "Default Type",
            "level": level_name or "Level 1",
            "location": location or [0.0, 0.0, 0.0]
        }
    }


@register_command("modify_element")
def modify_element(server, element_id, parameters=None, location=None):
    """Modify an existing element in the Revit project"""
    # This would use the Revit API to modify an element
    # For example:
    # from Autodesk.Revit.DB import ElementId, Document, Transaction
    # doc = __revit__.ActiveUIDocument.Document
    # transaction = Transaction(doc, "Modify Element")
    # transaction.Start()
    # element = doc.GetElement(ElementId(int(element_id)))
    # ... modify element logic here ...
    # transaction.Commit()
    
    # For demonstration, return dummy data
    return {
        "result": "success",
        "message": f"Modified element {element_id}",
        "element": {
            "id": element_id,
            "parameters_modified": parameters or {},
            "location_modified": location is not None,
            "new_location": location or [0.0, 0.0, 0.0]
        }
    }


@register_command("delete_element")
def delete_element(server, element_id):
    """Delete an element from the Revit project"""
    # This would use the Revit API to delete an element
    # For example:
    # from Autodesk.Revit.DB import ElementId, Document, Transaction
    # doc = __revit__.ActiveUIDocument.Document
    # transaction = Transaction(doc, "Delete Element")
    # transaction.Start()
    # doc.Delete(ElementId(int(element_id)))
    # transaction.Commit()
    
    # For demonstration, return dummy data
    return {
        "result": "success",
        "message": f"Deleted element {element_id}"
    }


@register_command("get_views")
def get_views(server):
    """Get a list of all views in the current Revit project"""
    # This would use the Revit API to get views
    # For example:
    # from Autodesk.Revit.DB import FilteredElementCollector, View
    # doc = __revit__.ActiveUIDocument.Document
    # views = FilteredElementCollector(doc).OfClass(View).ToElements()
    
    # For demonstration, return dummy data
    return {
        "views": [
            {
                "id": "100001",
                "name": "Floor Plan: Level 1",
                "type": "FloorPlan",
                "scale": "1:100"
            },
            {
                "id": "100002",
                "name": "Floor Plan: Level 2",
                "type": "FloorPlan",
                "scale": "1:100"
            },
            {
                "id": "100003",
                "name": "East Elevation",
                "type": "Elevation",
                "scale": "1:50"
            },
            {
                "id": "100004",
                "name": "Section A-A",
                "type": "Section",
                "scale": "1:50"
            },
            {
                "id": "100005",
                "name": "3D View",
                "type": "ThreeD",
                "scale": "1:100"
            }
        ]
    }


@register_command("get_elements_of_category")
def get_elements_of_category(server, category):
    """Get all elements of a specified category in the Revit project"""
    # This would use the Revit API to get elements by category
    # For example:
    # from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory
    # doc = __revit__.ActiveUIDocument.Document
    # if category.upper() == "WALLS":
    #     built_in_category = BuiltInCategory.OST_Walls
    # elements = FilteredElementCollector(doc).OfCategory(built_in_category).WhereElementIsNotElementType().ToElements()
    
    # For demonstration, return dummy data
    category_elements = []
    if category.lower() == "walls":
        category_elements = [
            {
                "id": "123456",
                "name": "Basic Wall",
                "type": "Generic - 8\"",
                "length": 20.0
            },
            {
                "id": "123457",
                "name": "Basic Wall",
                "type": "Exterior - Brick",
                "length": 30.0
            }
        ]
    elif category.lower() == "doors":
        category_elements = [
            {
                "id": "234567",
                "name": "Single-Flush",
                "type": "36\" x 84\"",
                "family": "Door-Single"
            },
            {
                "id": "234568",
                "name": "Double-Glass",
                "type": "72\" x 84\"",
                "family": "Door-Double"
            }
        ]
    
    return {
        "category": category,
        "count": len(category_elements),
        "elements": category_elements
    }


@register_command("create_dimension")
def create_dimension(server, element_ids, reference_points=None, view_id=None):
    """Create a dimension between elements in a view"""
    # This would use the Revit API to create dimensions
    # For example:
    # from Autodesk.Revit.DB import Dimension, Line, XYZ, ElementId, Transaction
    # doc = __revit__.ActiveUIDocument.Document
    # transaction = Transaction(doc, "Create Dimension")
    # transaction.Start()
    # ... dimension creation logic here ...
    # transaction.Commit()
    
    # For demonstration, return dummy data
    return {
        "result": "success",
        "message": f"Created dimension between {len(element_ids)} elements",
        "dimension": {
            "id": "345678",
            "elements": element_ids,
            "view": view_id or "Active View"
        }
    }


@register_command("execute_code")
def execute_code(server, code):
    """Execute custom Python code in the Revit environment"""
    # This is a powerful but potentially dangerous feature
    # For demonstration, log the code and return dummy data
    print(f"Executing custom code: {code}")
    
    # In a real implementation, this would use something like:
    # result = {}
    # try:
    #     exec_globals = {
    #         'doc': __revit__.ActiveUIDocument.Document,
    #         'uidoc': __revit__.ActiveUIDocument,
    #         'result': result
    #     }
    #     exec(code, exec_globals)
    # except Exception as e:
    #     return {"error": str(e)}
    
    return {
        "result": "success",
        "message": "Code executed successfully",
        "output": "Code execution output would appear here"
    }


@register_command("export_view")
def export_view(server, view_id, file_path, format="PNG"):
    """Export a specific view to an image file"""
    # This would use the Revit API to export views
    # For example:
    # from Autodesk.Revit.DB import ImageExportOptions, ElementId
    # doc = __revit__.ActiveUIDocument.Document
    # options = ImageExportOptions()
    # options.ViewName = view_id
    # options.FilePath = file_path
    # options.ImageResolution = ImageResolution.DPI_300
    # options.ExportRange = ExportRange.CurrentView
    # options.ZoomType = ZoomFitType.FitToPage
    # options.PixelSize = 2000
    # doc.ExportImage(options)
    
    # For demonstration, return dummy data
    return {
        "result": "success",
        "message": f"Exported view {view_id} to {file_path} in {format} format",
        "file": {
            "path": file_path,
            "format": format,
            "size": "1.5 MB",
            "dimensions": "2000x1500 px"
        }
    }


@register_command("create_sheet")
def create_sheet(server, sheet_name, sheet_number, titleblock_id=None):
    """Create a new sheet in the Revit project"""
    # This would use the Revit API to create sheets
    # For example:
    # from Autodesk.Revit.DB import ViewSheet, ElementId, Transaction
    # doc = __revit__.ActiveUIDocument.Document
    # transaction = Transaction(doc, "Create Sheet")
    # transaction.Start()
    # titleblock_id = ElementId(int(titleblock_id)) if titleblock_id else ElementId.InvalidElementId
    # sheet = ViewSheet.Create(doc, titleblock_id)
    # sheet.Name = sheet_name
    # sheet.SheetNumber = sheet_number
    # transaction.Commit()
    
    # For demonstration, return dummy data
    return {
        "result": "success",
        "message": f"Created sheet {sheet_name} with number {sheet_number}",
        "sheet": {
            "id": "456789",
            "name": sheet_name,
            "number": sheet_number,
            "titleblock": titleblock_id or "Default Titleblock"
        }
    }

# ======================================================================
# Revit Application Integration
# ======================================================================

# Global server instance
revit_mcp_server = None

def start_server():
    """Start the RevitMCP server"""
    global revit_mcp_server
    if revit_mcp_server is None:
        revit_mcp_server = RevitMCPServer()
        if revit_mcp_server.start():
            print("RevitMCP server started successfully")
            return True
        else:
            print("Failed to start RevitMCP server")
            revit_mcp_server = None
            return False
    else:
        print("RevitMCP server already running")
        return True

def stop_server():
    """Stop the RevitMCP server"""
    global revit_mcp_server
    if revit_mcp_server is not None:
        revit_mcp_server.stop()
        revit_mcp_server = None
        print("RevitMCP server stopped")
        return True
    else:
        print("RevitMCP server not running")
        return False

# ======================================================================
# Entry point for Revit plugin
# ======================================================================

def __init__(uiapp):
    """Initialize the RevitMCP addon when loaded into Revit"""
    try:
        # This is the entry point for PyRevit scripts
        from pyrevit import forms, script
        from pyrevit.framework import wpf

        # Create UI for the addin
        output = script.get_output()
        output.print_md("# RevitMCP Addon Loaded")
        output.print_md("This addon allows Claude AI to connect to Revit through the Model Context Protocol (MCP).")
        
        # Provide button to start/stop server
        if revit_mcp_server is None or not revit_mcp_server.running:
            output.print_md("Server Status: **Not Running**")
            forms.alert("To start the RevitMCP server, use the 'Start RevitMCP Server' button.", title="RevitMCP")
        else:
            output.print_md("Server Status: **Running**")
            
    except Exception as e:
        print(f"Error initializing RevitMCP addon: {str(e)}")
        traceback.print_exc()

# ======================================================================
# Commands to be exposed to PyRevit or Revit UI
# ======================================================================

def start_revit_mcp_server(sender, args):
    """UI command to start the server"""
    start_server()

def stop_revit_mcp_server(sender, args):
    """UI command to stop the server"""
    stop_server()

# ======================================================================
# UI definition (PyRevit format)
# ======================================================================

# For PyRevit integration, this would be in a separate file
# but included here for completeness

"""
<PushButton>
    <ButtonText>Start RevitMCP Server</ButtonText>
    <Script>
import addon
addon.start_revit_mcp_server(__commandbutton__, __eventargs__)
    </Script>
    <ToolTip>Start the RevitMCP server to connect with Claude AI</ToolTip>
</PushButton>

<PushButton>
    <ButtonText>Stop RevitMCP Server</ButtonText>
    <Script>
import addon
addon.stop_revit_mcp_server(__commandbutton__, __eventargs__)
    </Script>
    <ToolTip>Stop the RevitMCP server connection</ToolTip>
</PushButton>
""" 