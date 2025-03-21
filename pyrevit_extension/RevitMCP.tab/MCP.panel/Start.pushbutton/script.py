"""Start RevitMCP Server Button"""
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
    
    # Start the server
    result = addon.start_server()
    
    # Update the UI based on the result
    output = script.get_output()
    if result:
        output.print_md("# RevitMCP Server Started")
        output.print_md("The server is now running and ready to connect to Claude AI.")
        output.print_md("Server address: **localhost:9876**")
        forms.alert("RevitMCP server started successfully.", title="RevitMCP")
    else:
        output.print_md("# Failed to Start RevitMCP Server")
        output.print_md("Please check the console for error messages.")
        forms.alert("Failed to start the RevitMCP server. Check the console for details.", title="RevitMCP Error")
    
except Exception as e:
    forms.alert(f"Error starting RevitMCP server: {str(e)}", title="RevitMCP Error") 