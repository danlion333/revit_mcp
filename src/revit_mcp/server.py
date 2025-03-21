# revit_mcp_server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RevitMCPServer")

@dataclass
class RevitConnection:
    host: str
    port: int
    sock: socket.socket = None
    
    def connect(self) -> bool:
        """Connect to the Revit addon socket server"""
        if self.sock:
            return True
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Revit at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Revit: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Revit addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Revit: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # Use a consistent timeout value
        sock.settimeout(15.0)
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Revit and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Revit")
        
        command = {
            "type": command_type,
            "params": params or {}
        }
        
        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(15.0)
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Revit error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Revit"))
            
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Revit")
            self.sock = None
            raise Exception("Timeout waiting for Revit response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Revit lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Revit: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Revit: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Revit: {str(e)}")
            self.sock = None
            raise Exception(f"Communication error with Revit: {str(e)}")


# Create the MCP server
mcp = FastMCP(name="revit")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Handle server lifecycle"""
    connection = None
    
    try:
        # Initialize connection
        connection = RevitConnection(host='localhost', port=9876)
        if not connection.connect():
            logger.warning("Failed to connect to Revit on startup - will try on first command")
            
        # Initialize the server context
        yield {"revit_connection": connection}
    except Exception as e:
        logger.error(f"Error during server startup: {str(e)}")
        if connection:
            connection.disconnect()
    finally:
        # Cleanup when server shuts down
        if connection:
            connection.disconnect()

# Configure server with the lifespan handler
mcp.lifespan(server_lifespan)


def get_revit_connection():
    """Get the Revit connection from context or create a new one"""
    try:
        connection = mcp.state.get("revit_connection")
        
        # Initialize if not present
        if not connection:
            connection = RevitConnection(host='localhost', port=9876)
            mcp.state["revit_connection"] = connection
            
        # Test and potentially reconnect
        if not connection.sock and not connection.connect():
            logger.warning("Failed to connect to Revit - make sure the Revit addon is running")
            raise ConnectionError("Not connected to Revit")
            
        return connection
    except Exception as e:
        logger.error(f"Error getting Revit connection: {str(e)}")
        raise Exception(f"Failed to establish connection to Revit: {str(e)}")


# MCP Tools for Revit

@mcp.tool()
def get_project_info(ctx: Context) -> str:
    """
    Get information about the current Revit project.
    
    Returns:
        Information about the current Revit project including project number, name, 
        client, project address, etc.
    """
    connection = get_revit_connection()
    result = connection.send_command("get_project_info")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_element_info(ctx: Context, element_id: str) -> str:
    """
    Get detailed information about a specific Revit element.
    
    Args:
        element_id: The ID of the Revit element to get information for
        
    Returns:
        Detailed information about the element including its properties, parameters, 
        geometry, and relationships.
    """
    connection = get_revit_connection()
    result = connection.send_command("get_element_info", {"element_id": element_id})
    return json.dumps(result, indent=2)


@mcp.tool()
def create_element(
    ctx: Context,
    element_type: str,
    family_name: str = None,
    type_name: str = None,
    parameters: Dict[str, Any] = None,
    location: List[float] = None,
    level_name: str = None
) -> str:
    """
    Create a new element in the Revit project.
    
    Args:
        element_type: The type of element to create (e.g., "Wall", "Door", "Window")
        family_name: Optional name of the family to use
        type_name: Optional name of the type to use
        parameters: Optional dictionary of parameter values for the new element
        location: Optional 3D coordinates [x, y, z] for placement
        level_name: Optional name of the level to place the element on
        
    Returns:
        Information about the newly created element, including its ID.
    """
    connection = get_revit_connection()
    params = {
        "element_type": element_type
    }
    
    if family_name:
        params["family_name"] = family_name
    if type_name:
        params["type_name"] = type_name
    if parameters:
        params["parameters"] = parameters
    if location:
        params["location"] = location
    if level_name:
        params["level_name"] = level_name
        
    result = connection.send_command("create_element", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def modify_element(
    ctx: Context,
    element_id: str,
    parameters: Dict[str, Any] = None,
    location: List[float] = None
) -> str:
    """
    Modify an existing element in the Revit project.
    
    Args:
        element_id: The ID of the element to modify
        parameters: Optional dictionary of parameter values to update
        location: Optional new 3D coordinates [x, y, z] for placement
        
    Returns:
        Updated information about the modified element.
    """
    connection = get_revit_connection()
    params = {
        "element_id": element_id
    }
    
    if parameters:
        params["parameters"] = parameters
    if location:
        params["location"] = location
        
    result = connection.send_command("modify_element", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def delete_element(ctx: Context, element_id: str) -> str:
    """
    Delete an element from the Revit project.
    
    Args:
        element_id: The ID of the element to delete
        
    Returns:
        Confirmation of the deletion.
    """
    connection = get_revit_connection()
    result = connection.send_command("delete_element", {"element_id": element_id})
    return json.dumps(result, indent=2)


@mcp.tool()
def get_views(ctx: Context) -> str:
    """
    Get a list of all views in the current Revit project.
    
    Returns:
        A list of views in the project with their types and properties.
    """
    connection = get_revit_connection()
    result = connection.send_command("get_views")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_elements_of_category(ctx: Context, category: str) -> str:
    """
    Get all elements of a specified category in the Revit project.
    
    Args:
        category: The category name (e.g., "Walls", "Doors", "Windows")
        
    Returns:
        A list of elements in the specified category with basic information.
    """
    connection = get_revit_connection()
    result = connection.send_command("get_elements_of_category", {"category": category})
    return json.dumps(result, indent=2)


@mcp.tool()
def create_dimension(
    ctx: Context,
    element_ids: List[str],
    reference_points: List[List[float]] = None,
    view_id: str = None
) -> str:
    """
    Create a dimension between elements in a view.
    
    Args:
        element_ids: List of element IDs to dimension
        reference_points: Optional list of 3D points to use as references
        view_id: Optional ID of the view to place the dimension in
        
    Returns:
        Information about the created dimension.
    """
    connection = get_revit_connection()
    params = {
        "element_ids": element_ids
    }
    
    if reference_points:
        params["reference_points"] = reference_points
    if view_id:
        params["view_id"] = view_id
        
    result = connection.send_command("create_dimension", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def execute_revit_code(ctx: Context, code: str) -> str:
    """
    Execute custom Python code in the Revit environment.
    
    Args:
        code: The Python code to execute
        
    Returns:
        Output from code execution.
    """
    connection = get_revit_connection()
    result = connection.send_command("execute_code", {"code": code})
    return json.dumps(result, indent=2)


@mcp.tool()
def export_view(
    ctx: Context,
    view_id: str,
    file_path: str,
    format: str = "PNG"
) -> str:
    """
    Export a specific view to an image file.
    
    Args:
        view_id: The ID of the view to export
        file_path: The path where the exported file should be saved
        format: The export format (PNG, JPG, PDF, DWG)
        
    Returns:
        Information about the exported file.
    """
    connection = get_revit_connection()
    params = {
        "view_id": view_id,
        "file_path": file_path,
        "format": format
    }
    
    result = connection.send_command("export_view", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def create_sheet(
    ctx: Context,
    sheet_name: str,
    sheet_number: str,
    titleblock_id: str = None
) -> str:
    """
    Create a new sheet in the Revit project.
    
    Args:
        sheet_name: The name for the new sheet
        sheet_number: The sheet number
        titleblock_id: Optional ID of a titleblock to use
        
    Returns:
        Information about the newly created sheet.
    """
    connection = get_revit_connection()
    params = {
        "sheet_name": sheet_name,
        "sheet_number": sheet_number
    }
    
    if titleblock_id:
        params["titleblock_id"] = titleblock_id
        
    result = connection.send_command("create_sheet", params)
    return json.dumps(result, indent=2)


@mcp.prompt()
def revit_design_strategy() -> str:
    """
    Guidelines for effective Revit design workflows when using the MCP.
    """
    return """
    # Revit Design Strategy with MCP
    
    ## Effective Workflow
    
    1. **Analyze Requirements First**: Before creating elements, understand the project requirements and constraints.
    
    2. **Start with Basic Structure**: Begin with levels, grids, structural elements, then add architectural elements.
    
    3. **Use Parameter-Driven Design**: Leverage parameters to control element properties and maintain consistency.
    
    4. **Reuse Existing Types**: When possible, use existing family types before creating custom ones.
    
    5. **Group Related Operations**: Batch similar commands together for efficiency.
    
    ## Common Approaches for Design Tasks
    
    - **Basic Building Layout**: Start with levels and grids, then add walls, floors, and structural elements
    
    - **Architectural Detailing**: Add doors, windows, fixtures, and finishes after basic layout is complete
    
    - **Mechanical/Electrical Systems**: Coordinate with architectural elements, maintain proper clearances
    
    - **Documentation**: Create appropriate views, add dimensions and annotations, organize sheets logically
    
    ## Tips for Specific Element Types
    
    - **Walls**: Consider wall function (structural, exterior, interior), required fire rating, and finishes
    
    - **Doors/Windows**: Verify type compatibility with host walls and proper dimensions
    
    - **MEP Elements**: Check for interference with other building elements
    
    - **Structural Elements**: Ensure proper connections and load-bearing capacity
    """


def main():
    """Launch the Revit MCP server"""
    logging.info("Starting Revit MCP server...")
    mcp.start()


if __name__ == "__main__":
    main() 