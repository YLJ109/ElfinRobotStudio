// User naming: old left -> front, old back -> left. XYZ/angles stay fixed.
// Display-only labels never change the robot or its control coordinates.
export const GROUND_DIRECTIONS = [
  {name:'右侧', angle:0, xyz:[0,-1150,0]},
  {name:'背面', angle:90, xyz:[1150,0,0]},
  {name:'左侧', angle:180, xyz:[0,1150,0]},
  {name:'正面', angle:270, xyz:[-1150,0,0]},
]
