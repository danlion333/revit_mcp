# RevitMCP - Autodesk Revit Model Context Protocol Integration

RevitMCP connects Autodesk Revit to Claude AI through the Model Context Protocol (MCP), allowing Claude to directly interact with and control Revit. This integration enables prompt-assisted architectural modeling, building information management, and documentation.

## Features

- **Two-way communication**: Connect Claude AI to Revit through a socket-based server
- **Element manipulation**: Create, modify, and delete Revit elements (walls, doors, windows, etc.)
- **Parameter control**: Set and modify parameters of Revit elements
- **Project inspection**: Get detailed information about the current Revit project and elements
- **Documentation**: Create and manage views, sheets, and dimensions
- **Code execution**: Run arbitrary Python code in the Revit environment from Claude

## Components

The system consists of two main components:

1. **Revit Addon (`addon.py`)**: A Revit addon that creates a socket server within Revit to receive and execute commands
2. **MCP Server (`src/revit_mcp/server.py`)**: A Python server that implements the Model Context Protocol and connects to the Revit addon

## Installation

### Prerequisites

- Autodesk Revit 2019 or newer
- Python 3.10 or newer
- PyRevit for Revit integration
- uv package manager

Install uv package manager:

**On Mac**
```bash
brew install uv
```

**On Windows**
```bash
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```
and then
```bash
set Path=C:\Users\[username]\.local\bin;%Path%
```

Otherwise, installation instructions are on their website: [Install uv](https://docs.astral.sh/uv/getting-started/installation/)

### Claude for Desktop Integration

Go to Claude > Settings > Developer > Edit Config > claude_desktop_config.json to include the following:

```json
{
    "mcpServers": {
        "revit": {
            "command": "uvx",
            "args": [
                "revit-mcp"
            ]
        }
    }
}
```

### Cursor Integration

Run revit-mcp without installing it permanently through uvx. Go to Cursor Settings > MCP and paste this as a command.

```bash
uvx revit-mcp
```

**⚠️ Only run one instance of the MCP server (either on Cursor or Claude Desktop), not both**

### Installing the Revit Addon

#### Option 1: PyRevit Extension

1. Download the `addon.py` file from this repo
2. Create a new PyRevit extension with the following structure:
   ```
   RevitMCP.extension/
   └── RevitMCP.tab/
       └── MCP.panel/
           ├── Start.pushbutton/
           │   └── script.py  # Import and call start_server from addon.py
           └── Stop.pushbutton/
               └── script.py  # Import and call stop_server from addon.py
   ```
3. Copy the addon.py file to the extension directory
4. Edit the script.py files to import and call the appropriate functions

#### Option 2: Standalone Python Script

If you don't want to use PyRevit:

1. Download the `addon.py` file
2. Open Revit and use the Revit Python Shell or similar tool to execute:
   ```python
   import sys
   sys.path.append("path/to/directory/containing/addon.py")
   import addon
   addon.start_server()
   ```

## Usage

### Starting the Connection

1. In Revit, run the "Start RevitMCP Server" command from the PyRevit tab
2. You should see a message confirming the server is running
3. Make sure the MCP server is running in your terminal

### Using with Claude

Once the config file has been set on Claude, and the addon is running on Revit, you will see a hammer icon with tools for the Revit MCP.

#### Tools

- `get_project_info` - Gets information about the current Revit project
- `get_element_info` - Gets detailed information for a specific element
- `create_element` - Create a new element with detailed parameters
- `modify_element` - Modify an existing element's properties
- `delete_element` - Remove an element from the project
- `get_views` - Get a list of all views in the project
- `get_elements_of_category` - Get all elements of a specified category
- `create_dimension` - Create dimensions between elements
- `execute_revit_code` - Run any Python code in Revit
- `export_view` - Export a specific view to an image file
- `create_sheet` - Create a new sheet in the project

### Example Commands

Here are some examples of what you can ask Claude to do:

- "Create a basic floor plan with exterior walls"
- "Add doors and windows to the north wall"
- "Get information about the current project"
- "Create a new sheet with the floor plan view"
- "Add dimensions between doors"
- "Export the 3D view as a PNG"

## Troubleshooting

- **Connection issues**: Make sure the Revit addon server is running, and the MCP server is configured on Claude
- **Timeout errors**: Try simplifying your requests or breaking them into smaller steps
- **PyRevit integration**: If you're having trouble with PyRevit, try running the addon as a standalone script
- **If all else fails**: Try restarting both Claude and the Revit server

## Technical Details

### Communication Protocol

The system uses a simple JSON-based protocol over TCP sockets:

- **Commands** are sent as JSON objects with a `type` and optional `params`
- **Responses** are JSON objects with a `status` and `result` or `message`

## Limitations & Security Considerations

- The `execute_revit_code` tool allows running arbitrary Python code in Revit, which can be powerful but potentially dangerous. Use with caution in production environments.
- Complex operations might need to be broken down into smaller steps.
- Always save your Revit project before using this tool.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Disclaimer

This is a third-party integration and not made by Autodesk or Revit.

## License

This project is licensed under the MIT License - see the LICENSE file for details. 