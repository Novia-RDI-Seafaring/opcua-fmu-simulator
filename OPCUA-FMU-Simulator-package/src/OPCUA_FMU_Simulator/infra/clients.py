from asyncua import Client, ua
import logging
logging.basicConfig(level=logging.INFO) # required to enable log message print to terminal
logger = logging.getLogger(__name__)
import asyncio

class client_manager:
    @classmethod
    async def create(cls, system_servers, remote_servers, system_node_ids):
        self = cls()
        
        self.system_servers = system_servers
        self.remote_servers = remote_servers
        self.system_node_ids = system_node_ids
        
        self.system_clients     = {} 
        self.external_clients   = {}
        
        await self.creat_internal_clients()
        await self.create_external_clients()
        
        return self
    
        
    async def creat_internal_clients(self) -> None:
        for server_name in self.system_servers:
            server = self.system_servers[server_name]
            client = Client(url=server.url)
            await client.connect()
            self.system_clients[server_name] = client
        logger.info(f"system clients clients setup: {self.system_clients}")
        
    async def create_external_clients(self) -> None:
        # TODO: REMOVE SYSTEM NODE ID INITIALIZATION FROM HERE, SEPARATE THE FUNCTIONALITY
        for server in self.remote_servers:
            server_url = self.remote_servers[server]["url"]
            print(f"\n\nTRYING TO CONNECT TO {server_url} \n\n")
            client = Client(url=server_url)
            await client.connect()
            self.external_clients[server] = client
            self.system_node_ids[server] = {}
            for obj in self.remote_servers[server]["objects"]:
                for var in self.remote_servers[server]["objects"][obj]:
                    keys = self.remote_servers[server]["objects"][obj][var].keys()
                    if "name" in keys:
                        self.system_node_ids[server][var] = ua.NodeId(self.remote_servers[server]["objects"][obj][var]["name"])
                    elif("id" in keys and "ns" in keys):
                        id = self.remote_servers[server]["objects"][obj][var]["id"]
                        ns = self.remote_servers[server]["objects"][obj][var]["ns"]
                        self.system_node_ids[server][var] = ua.NodeId(Identifier= id, NamespaceIndex= ns)
                    else:
                        raise Exception(f"server {server} with object {obj} found no acceptable id namespace or name for variable {var}")

    def fetch_appropriacte_client(self, client_name)->Client:
        if client_name in self.system_clients.keys():     return self.system_clients[client_name]
        elif client_name in self.external_clients.keys(): return self.external_clients[client_name]
        else: raise Exception(f"UNKNOWN CLIENT {client_name}")
        
    async def get_system_values(self) -> dict:
            return self.system_clients.keys()
            
    async def reset_system(self) -> None:
        for client_name in self.system_clients:
            object_node = self.system_clients[client_name].get_node(ua.NodeId(1, 1))
            await object_node.call_method(ua.NodeId(1, 4))

    async def initialize_system_variables(self, test:dict) -> None:
        """
        initialize system variables base on input state
        uses user defined initial state
        """
        initial_system_state = test["initial_system_state"]
        for server in initial_system_state:
            for variable in initial_system_state[server]:
                object_node = self.system_clients[server].get_node(ua.NodeId(1, 1))
                update_values = {
                    "variable": variable,
                    "value": float(initial_system_state[server][variable])
                }
                await object_node.call_method(ua.NodeId(1, 3), str(update_values)) # update fmu before updating values

    async def close(self) -> None:
        """ 
        disconnects all clients to enable the setup of new ones
        releases ports
        """
        if(len(self.external_clients)):
            await asyncio.gather(
                *(c.disconnect() for c in self.external_clients.values()),
            )
            self.external_clients.clear()
        
        if(len(self.system_clients)):    
            await asyncio.gather(
                *(c.disconnect() for c in self.system_clients.values()),
            )
            self.system_clients.clear()
            