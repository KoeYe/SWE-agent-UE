#!/usr/bin/env python3
"""
Enhanced argument parser for MCP tools that supports both JSON and command-line style arguments.
"""
import argparse
import json
import re
import sys
import shlex
from typing import Dict, Any, List


class MCPArgumentParser:
    """Parser for MCP tool arguments supporting both JSON and CLI-style formats."""
    
    def __init__(self):
        self.supported_tools = {
            'api_doc_query': ['query'],
            'execute_python_script': ['script', 'path'],
            'list_python_scripts': [],
            'get_camera_0_view': [],
            'move_camera': ['location', 'rotation'],
        }
    
    def parse_args(self, tool_name: str, args_str: str) -> Dict[str, Any]:
        """
        Parse arguments for the given tool.
        
        Args:
            tool_name: Name of the MCP tool
            args_str: Arguments string (JSON or CLI-style)
            
        Returns:
            Dictionary of parsed arguments
        """
        if not args_str.strip():
            return {}
        
        # First try to parse as JSON (both simple and nested formats)
        json_result = self._try_parse_json(args_str.strip())
        if json_result is not None:
            return json_result
        
        # If not JSON, try command-line style arguments
        return self._parse_cli_style(tool_name, args_str)
    
    def _try_parse_json(self, args_str: str) -> Dict[str, Any] | None:
        """Try to parse as JSON format."""
        try:
            # First, try to parse as-is
            result = json.loads(args_str)
        except json.JSONDecodeError:
            # If that fails, try preprocessing for shell double-escaping
            try:
                # Fix shell double-escaping issues: \\n -> \n, \\" -> "
                preprocessed = args_str.replace('\\\\n', '\\n').replace('\\\\"', '\\"').replace('\\\\t', '\\t').replace('\\\\r', '\\r')
                result = json.loads(preprocessed)
            except json.JSONDecodeError:
                return None
        
        # Handle nested structure like {"type": "execute_python_script", "params": {"script": "..."}}
        if isinstance(result, dict):
            # Check if it's the nested format
            if "type" in result and "params" in result and isinstance(result["params"], dict):
                # Extract params and process escape sequences if it's a script
                params = result["params"]
                if "script" in params:
                    params["script"] = self._process_escape_sequences(params["script"])
                return params
            else:
                # Standard format, process escape sequences for script parameter
                if "script" in result:
                    result["script"] = self._process_escape_sequences(result["script"])
                return result
        else:
            return {"value": result}
    
    def _parse_cli_style(self, tool_name: str, args_str: str) -> Dict[str, Any]:
        """Parse command-line style arguments like --query="value" --script="code"."""
        result = {}
        
        # Handle special cases for execute_python_script
        if tool_name == 'execute_python_script':
            return self._parse_execute_python_script(args_str)
        
        # Generic CLI argument parsing
        try:
            # Split arguments respecting quotes
            args_list = shlex.split(args_str)
            
            i = 0
            while i < len(args_list):
                arg = args_list[i]
                
                if arg.startswith('--'):
                    # Handle --param=value format
                    if '=' in arg:
                        key, value = arg[2:].split('=', 1)
                        result[key] = self._process_value(value)
                    # Handle --param value format
                    elif i + 1 < len(args_list):
                        key = arg[2:]
                        i += 1
                        value = args_list[i]
                        result[key] = self._process_value(value)
                    else:
                        # Boolean flag
                        result[arg[2:]] = True
                else:
                    # Positional argument - map to first expected parameter
                    if tool_name in self.supported_tools:
                        expected_params = self.supported_tools[tool_name]
                        if expected_params:
                            # Use first parameter as default
                            key = expected_params[0]
                            result[key] = self._process_value(arg)
                
                i += 1
                
        except ValueError as e:
            # Fallback: treat entire string as value for first parameter
            if tool_name in self.supported_tools:
                expected_params = self.supported_tools[tool_name]
                if expected_params:
                    result[expected_params[0]] = args_str.strip()
                else:
                    result['value'] = args_str.strip()
            else:
                result['value'] = args_str.strip()
        
        return result
    
    def _parse_execute_python_script(self, args_str: str) -> Dict[str, Any]:
        """Special parsing for execute_python_script with script content handling."""
        
        # Handle --script="$(cat filename)" format
        cat_match = re.search(r'--script=["\']?\$\(cat\s+([^)]+)\)["\']?', args_str)
        if cat_match:
            filename = cat_match.group(1).strip().strip('"\'')
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    return {'script': f.read()}
            except (FileNotFoundError, IOError):
                return {'script': f'# File not found: {filename}'}
        
        # Handle --path="filename" format
        path_match = re.search(r'--path=["\']?([^"\s]+)["\']?', args_str)
        if path_match:
            return {'path': path_match.group(1)}
        
        # Handle --script="code" format
        script_match = re.search(r'--script=["\']?(.*?)["\']?$', args_str, re.DOTALL)
        if script_match:
            script_content = script_match.group(1)
            # Remove outer quotes if present
            if script_content.startswith('"') and script_content.endswith('"'):
                script_content = script_content[1:-1]
            elif script_content.startswith("'") and script_content.endswith("'"):
                script_content = script_content[1:-1]
            
            # Process escape sequences
            script_content = self._process_escape_sequences(script_content)
            return {'script': script_content}
        
        # Fallback: treat as script content and process escape sequences
        processed_content = self._process_escape_sequences(args_str)
        return {'script': processed_content}
    
    def _process_value(self, value: str) -> Any:
        """Process a value, handling quotes and type conversion."""
        # Remove outer quotes
        if len(value) >= 2:
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
        
        # Try to convert to appropriate type
        if value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        
        try:
            # Try integer
            if value.isdigit() or (value.startswith('-') and value[1:].isdigit()):
                return int(value)
        except ValueError:
            pass
        
        try:
            # Try float
            return float(value)
        except ValueError:
            pass
        
        # Return as string
        return value
    
    def _process_escape_sequences(self, text: str) -> str:
        """Process escape sequences in text, converting \\n to actual newlines etc."""
        if not text:
            return text
        
        # Handle common escape sequences
        replacements = {
            '\\n': '\n',    # Newline
            '\\t': '\t',    # Tab
            '\\r': '\r',    # Carriage return
            '\\"': '"',     # Escaped double quote
            "\\'": "'",     # Escaped single quote
            '\\\\': '\\',   # Escaped backslash
        }
        
        result = text
        for escaped, actual in replacements.items():
            result = result.replace(escaped, actual)
        
        return result


def create_mcp_parser() -> MCPArgumentParser:
    """Factory function to create MCP argument parser."""
    return MCPArgumentParser()


# For backward compatibility
def robust_parse_third_arg(args_str: str, tool_name: str) -> Dict[str, Any]:
    """Backward compatibility function."""
    parser = create_mcp_parser()
    return parser.parse_args(tool_name, args_str)


if __name__ == '__main__':
    # Test the parser
    parser = create_mcp_parser()
    
    test_cases = [
        ('api_doc_query', '--query="unreal.Vector usage"'),
        ('execute_python_script', '--script="print(\'hello world\')"'),
        ('execute_python_script', '--path="test.py"'),
        ('execute_python_script', '--script="$(cat record_camera.py)"'),
        ('api_doc_query', '{"query": "test"}'),  # JSON format
    ]
    
    for tool_name, args_str in test_cases:
        print(f"\n=== {tool_name} with args: {args_str} ===")
        result = parser.parse_args(tool_name, args_str)
        print(f"Result: {result}")