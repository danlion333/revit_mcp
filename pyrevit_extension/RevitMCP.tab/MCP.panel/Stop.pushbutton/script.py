"""Stop RevitMCP Server Button"""
from pyrevit import forms, script
import sys
import os

# Add the addon directory to the system path
# Change this path to the location of your addon.py file
addon_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

if addon_directory not in sys.path:
    sys.path.append(addon_directory)

try:
    import addon
    
    # Stop the server
    result = addon.stop_server()
    
    # Update the UI based on the result
    output = script.get_output()
    if result:
        output.print_md("# RevitMCP Server Stopped")
        output.print_md("The server has been successfully stopped.")
        forms.alert("RevitMCP server stopped successfully.", title="RevitMCP")
    else:
        output.print_md("# RevitMCP Server Not Running")
        output.print_md("The server was not running.")
        forms.alert("The RevitMCP server was not running.", title="RevitMCP")
    
except Exception as e:
    forms.alert(f"Error stopping RevitMCP server: {str(e)}", title="RevitMCP Error") 