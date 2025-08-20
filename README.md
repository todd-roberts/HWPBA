# Horizon Worlds Parts-Based Animation (HWPBA)

Lightweight tools + workflow for building parts-based animated characters in Horizon Worlds. Export from Blender → upload FBXs + a single Text Asset → drive animations in HW with a small script.

## Watch the tutorials

Two short series walk through the pipeline end-to-end:

1) Robot — intro to parts-based animation, auto-rigging, animation, importing assets, and playing an animation.
   Playlist: <PUT_YOUTUBE_LINK_HERE>

2) Egyptian Spider — breaking up a more complex, soft-bodied creature into parts, and a more interesting animation.
   Playlist: COMING SOON (within 72ish hours of you reading this)

Follow along using the starter files below.

## Download to follow along

- [Robot starter .blend](https://raw.githubusercontent.com/todd-roberts/HWPBA/main/blendFiles/BeepyBoop.zip)  

- [Blender extension (zip)](https://raw.githubusercontent.com/todd-roberts/HWPBA/main/hwpbaExtension.zip) — You can just drag this into Blender to install.  
  
## Scripting

Search the Horizon Worlds public assets for `PartsBasedAnimationSystem` to add the core script to your world. The tutorials above demonstrate how to script against the system.

## Scaling issue / fix

Parts-based characters do not scale in the Y axis by default. You can see this within Blender or your worlds. This is because parts need independent origins (pivot points) and as such scaling in the Y axis will cause them to drift apart as they scale at different rates.

![The spood's abdomen is disconnected and goes through the ground](<ScalingIssue.png>)

There are two simple solutions:

1. Design your character purposefully in Blender at the scale you want it to be for your world, such that you won't ever need to scale it. If you are starting with a character that is not separated, this requires scaling *before* breaking it up into parts and setting their respective origins.

This is the preferred option, however, it prevents the possibility of re-using the parts/animations for different-sized or themed characters, which can be incredibly powerful and time-saving.

2. Use the ScalingBase entity that will be included in the public `PartsBasedAnimationSystem` asset. This is a special wrapper you can place your character within that will allow you to scale the character up or down as much as you want while keeping it on the ground plane.

![Fixed!](<ScalingFix.png>)

Note that, if you need to programmatically scale your character at runtime, your scripts will need to modify the scale of the ScalingBase entity and not the character itself.

## Limitations
- The system is designed for parts-based animation, not vertex-based animation. 
- Currently scaling within the animations themselves is not suppported. That will be added in v2.

## Having trouble?

Open an issue on this repo with:
- Blender version + OS
- Steps to reproduce
- (Optional) a small .blend or screenshots


---

License and contributions: PRs welcome. Use at your own risk.