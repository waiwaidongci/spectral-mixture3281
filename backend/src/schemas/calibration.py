from pydantic import BaseModel,Field
class EndmemberCalibrationInput(BaseModel):
    code:str=Field(min_length=1,max_length=40)
    name:str=Field(default="",max_length=80)
    spectrum:list[float]=Field(min_length=2,max_length=64)
class CalibrationBatchInput(BaseModel):
    title:str=Field(default="",max_length=160)
    endmembers:list[EndmemberCalibrationInput]=Field(min_length=2,max_length=6)
