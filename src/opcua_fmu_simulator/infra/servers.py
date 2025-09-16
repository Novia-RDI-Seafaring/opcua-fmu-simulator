from opcua_fmu_simulator.config_loader import DataLoaderClass
from opcua_fmu_simulator.server_setup_dev import OPCUAFMUServerSetup
from pathlib import Path
import asyncio

class server_manager:
    # TODO: pass directly "FMU FILES" not whole experiment config file
    @classmethod
    async def create(cls, experiment_config):
        self = cls()
        self.remote_servers = self.construct_remote_servers(experiment_config["external_servers"])
        self.fmu_files = experiment_config["fmu_files"]
        self._tasks: list[asyncio.Task] = []
        self.system_servers: dict[str, OPCUAFMUServerSetup] = {}
        self.base_port = 7000
        await self.initialize_fmu_opc_servers()
        return self
    
    def construct_remote_servers(self, remote_servers: list[str]) -> dict[str:DataLoaderClass]:
        """
        remote_servers = path to directory with remote server definitions
        this function iterates through all of them and adds them to a dictionaty in a structured manner
        """
        server_dict = {}

        for server_name in remote_servers:
            server_desription = DataLoaderClass(server_name).data
            name = Path(server_name).stem
            server_dict[name] = server_desription
            
        return server_dict

    async def initialize_fmu_opc_servers(self) -> None:
        for fmu_file in self.fmu_files:
            self.base_port+=1
            server =  await OPCUAFMUServerSetup.async_server_init(
                fmu=fmu_file, 
                port=self.base_port
            )
            server_task = asyncio.create_task(server.main_loop())
            await server.server_started.wait()
            server.server_started.clear()
            self.system_servers[server.fmu.fmu_name] = server
            self._tasks.append(server_task)
        
    async def close(self) -> None:
        """Stop all servers and free the ports."""
        for srv in self.system_servers.values():
            await srv.server.stop()          # closes socket listener
        for t in self._tasks:                # background loops
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.system_servers.clear()