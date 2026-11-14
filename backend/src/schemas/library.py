from pydantic import BaseModel
class LibraryOutput(BaseModel):
    id:int
    code:str
    name:str
    description:str
    wavelengths:list[float]
    source:str
    calibration_version:int=1
    endmember_count:int
    sample_count:int
    supported:bool
    condition_number:float|None
    reason:str
